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
            
            # logger.info(f"Search results (limit={limit}, candidates={len(results)}): {', '.join(debug_results)}")
            # logger.info(f"Filtered results: {len(unique_documents)} docs (threshold={DISTANCE_THRESHOLD})")
            
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
        owner_id: int,
        is_temporary: bool = False
    ) -> str:
        """
        Upload file to storage and return image_url (no database operations)
        
        This is the first step in the separated upload/process flow.
        Handles file upload to storage backend only.
        
        Args:
            file_content: Image file content (BytesIO)
            filename: Original filename
            owner_id: Document owner user ID (for folder organization)
            is_temporary: If True, upload to tmp/ folder for preview (can be deleted if not confirmed)
            
        Returns:
            image_url of the uploaded file
            
        Raises:
            ValueError: If operation fails
        """
        try:
            # Upload image file to storage
            upload_type = "temporary (preview)" if is_temporary else "permanent"
            logger.info(f"Uploading file to storage ({upload_type}): filename={filename}, owner_id={owner_id}")
            image_url = StorageClient.upload_image(
                file_content=file_content,
                filename=filename,
                folder=f"documents/{owner_id}/pages",
                is_temporary=is_temporary
            )
            
            logger.info(f"File uploaded to storage. URL: {image_url}")
            logger.info(f"URL type: {'Supabase' if image_url.startswith('http') else 'Local'}, temporary={is_temporary}")
            return image_url
            
        except Exception as e:
            logger.error(f"Failed to upload file to storage: {e}")
            raise ValueError(f"File upload failed: {str(e)}")

    @staticmethod
    def _parse_location_tags(description: Optional[str]) -> list:
        """Extract tags (lowercase) from '[Tags: Spices, Flour] ...' — used for matching."""
        if not description:
            return []
        import re
        match = re.search(r'\[Tags:\s*([^\]]+)\]', description, re.IGNORECASE)
        if not match:
            return []
        return [t.strip().lower() for t in match.group(1).split(',') if t.strip()]

    @staticmethod
    def _parse_location_tags_cased(description: Optional[str]) -> list:
        """Extract tags preserving original case from '[Tags: Spices, Flour] ...'."""
        if not description:
            return []
        import re
        match = re.search(r'\[Tags:\s*([^\]]+)\]', description, re.IGNORECASE)
        if not match:
            return []
        return [t.strip() for t in match.group(1).split(',') if t.strip()]

    @staticmethod
    def _parse_excl_tags(description: Optional[str]) -> list:
        """Extract explicitly excluded tags (original case) from '[Excl: Snacks, ...]'."""
        if not description:
            return []
        import re
        match = re.search(r'\[Excl:\s*([^\]]+)\]', description, re.IGNORECASE)
        if not match:
            return []
        return [t.strip() for t in match.group(1).split(',') if t.strip()]

    @staticmethod
    def _strip_description_markers(description: Optional[str]) -> str:
        """Strip [Tags: ...] and [Excl: ...] markers, returning plain text only."""
        if not description:
            return ''
        import re
        text = re.sub(r'\[Tags:[^\]]*\]\s*', '', description, flags=re.IGNORECASE)
        text = re.sub(r'\[Excl:[^\]]*\]\s*', '', text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _rebuild_description(tags: list, excl_tags: list, free_text: str) -> str:
        """Rebuild a description string from its component parts."""
        tag_part = f"[Tags: {', '.join(tags)}] " if tags else ""
        excl_part = f"[Excl: {', '.join(excl_tags)}] " if excl_tags else ""
        return (tag_part + excl_part + free_text.strip()).strip()

    @staticmethod
    def _match_location_by_tag(db: Session, owner_id: int, sub_category: Optional[str],
                               storage_suggestion: Optional[str]) -> Optional[int]:
        """
        Level-1: Find the best location by matching sub_category tag.

        Priority within Level-1:
          1a. Manual [Tags: ...] in description (user-curated, highest trust)
          1b. content_analysis.top_sub_tags (auto-learned from history, fallback)

        When multiple matches, prefer the one whose name aligns with storage_suggestion.
        """
        if not sub_category:
            return None
        tag_target = sub_category.strip().lower()
        all_locations = db.query(StorageLocation).filter(
            StorageLocation.user_id == owner_id
        ).all()

        manual_candidates = []
        learned_candidates = []

        for loc in all_locations:
            manual_tags = DocumentService._parse_location_tags(loc.description)
            if tag_target in manual_tags:
                manual_candidates.append(loc)
                continue
            # Fall back to learned tags from content_analysis
            analysis = loc.content_analysis or {}
            learned_tags = [t.lower() for t in analysis.get("top_sub_tags", [])]
            if tag_target in learned_tags:
                learned_candidates.append(loc)

        candidates = manual_candidates if manual_candidates else learned_candidates
        if not candidates:
            return None

        if len(candidates) == 1:
            source = "manual" if manual_candidates else "learned"
            logger.info(
                f"[Location L1] {source} tag '{sub_category}' -> location_id={candidates[0].id}"
            )
            return candidates[0].id

        # Ambiguity: prefer candidate whose name aligns with storage_suggestion
        if storage_suggestion:
            hint = storage_suggestion.lower()
            for loc in candidates:
                if hint in loc.name.lower() or loc.name.lower() in hint:
                    return loc.id
        return candidates[0].id

    @staticmethod
    def _match_location_by_name(db: Session, owner_id: int, location_name: str) -> Optional[int]:
        """
        Level-2: Case-insensitive substring matching between storage_suggestion
        and existing location names (more lenient than ilike exact match).
        Handles 'Freezer' matching 'My Freezer' or 'Deep Freezer'.
        """
        if not location_name:
            return None
        name_lower = location_name.lower()
        all_locations = db.query(StorageLocation).filter(
            StorageLocation.user_id == owner_id
        ).all()
        for loc in all_locations:
            loc_name_lower = loc.name.lower()
            if name_lower in loc_name_lower or loc_name_lower in name_lower:
                return loc.id
        return None

    @staticmethod
    def _resolve_location(db: Session, owner_id: int, storage_suggestion: str,
                          sub_category: Optional[str] = None) -> Optional[int]:
        """
        Two-level location routing (no auto-creation):
          Level 1 — Tag match (highest priority): match sub_category against location
                    [Tags: ...] in description (manual) or content_analysis.top_sub_tags (learned).
          Level 2 — Name match (fallback): case-insensitive substring match between
                    storage_suggestion and existing location names.

        Returns None when no existing location matches — the caller is responsible
        for using the receipt's own location as the final fallback.
        """
        if not storage_suggestion:
            return None
        try:
            # Level 1: tag-based matching
            matched_id = DocumentService._match_location_by_tag(
                db, owner_id, sub_category, storage_suggestion
            )
            if matched_id:
                logger.info(
                    f"[Location L1] Tag match sub_category='{sub_category}' -> location_id={matched_id}"
                )
                return matched_id

            # Level 2: name substring matching
            matched_id = DocumentService._match_location_by_name(db, owner_id, storage_suggestion)
            if matched_id:
                logger.info(
                    f"[Location L2] Name match '{storage_suggestion}' -> location_id={matched_id}"
                )
                return matched_id

            # No match — caller falls back to receipt's location
            logger.info(
                f"[Location] No match for suggestion='{storage_suggestion}' sub='{sub_category}', "
                f"falling back to receipt location"
            )
            return None
        except Exception as e:
            logger.error(f"Failed to resolve location '{storage_suggestion}': {e}")
            return None

    @staticmethod
    def analyze_location(db: Session, location_id: int) -> Optional[dict]:
        """
        Build / refresh the content_analysis profile for a location.

        Profile schema:
        {
            "item_count": 12,
            "top_categories": ["MEAT", "DAIRY"],          # by frequency, up to 3
            "top_sub_tags":   ["Spices", "Baking"],        # from doc_metadata.sub_category, up to 3
            "dominant_category": "MEAT",
            "last_updated": "2026-02-23"
        }
        """
        try:
            from datetime import date
            from collections import Counter
            from sqlalchemy import func as sqlfunc

            location = db.query(StorageLocation).filter(
                StorageLocation.id == location_id
            ).first()
            if not location:
                return None

            from sqlalchemy import or_ as sql_or
            docs = (
                db.query(Document, DocumentCategory.code.label("cat_code"))
                .outerjoin(DocumentCategory, Document.category_id == DocumentCategory.id)
                .filter(Document.current_location_id == location_id)
                # Exclude receipt parent documents but keep items with no category (NULL join).
                # Without the NULL guard, "NULL NOT IN (...)" evaluates to NULL in SQL,
                # silently dropping uncategorised items from the count.
                .filter(sql_or(
                    Document.category_id == None,
                    ~DocumentCategory.code.in_(["RECEIPT", "REC"])
                ))
                .all()
            )

            item_count = len(docs)
            cat_counter: Counter = Counter()
            sub_counter: Counter = Counter()

            for doc, cat_code in docs:
                if cat_code:
                    cat_counter[cat_code.upper()] += 1
                if doc.doc_metadata:
                    sub = doc.doc_metadata.get("sub_category")
                    if sub:
                        sub_counter[sub] += 1

            ai_top_tags = [t for t, _ in sub_counter.most_common(3)]

            profile = {
                "item_count": item_count,
                "top_categories": [c for c, _ in cat_counter.most_common(3)],
                "top_sub_tags": ai_top_tags,
                "dominant_category": cat_counter.most_common(1)[0][0] if cat_counter else None,
                "last_updated": date.today().isoformat(),
            }

            location.content_analysis = profile
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(location, "content_analysis")

            # Merge AI-detected tags directly into location.description [Tags: ...].
            # This unifies manual and AI tags into a single source of truth.
            # Respect [Excl: ...] — never re-add tags the user explicitly dismissed.
            existing_tags = DocumentService._parse_location_tags_cased(location.description)
            excl_tags = DocumentService._parse_excl_tags(location.description)
            free_text = DocumentService._strip_description_markers(location.description)

            existing_lower = {t.lower() for t in existing_tags}
            excl_lower = {t.lower() for t in excl_tags}

            merged_tags = list(existing_tags)
            added = []
            for ai_tag in ai_top_tags:
                if ai_tag.lower() not in existing_lower and ai_tag.lower() not in excl_lower:
                    merged_tags.append(ai_tag)
                    existing_lower.add(ai_tag.lower())
                    added.append(ai_tag)

            if added:
                location.description = DocumentService._rebuild_description(
                    merged_tags, excl_tags, free_text
                )
                flag_modified(location, "description")
                logger.info(
                    f"[Intelligence] Location {location_id} AI merged tags {added} into description"
                )

            db.add(location)
            db.flush()
            logger.info(
                f"[Intelligence] Location {location_id} profile updated: "
                f"dominant={profile['dominant_category']}, items={item_count}, "
                f"ai_tags={ai_top_tags}"
            )
            return profile
        except Exception as e:
            logger.error(f"Failed to analyze location {location_id}: {e}", exc_info=True)
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
            image_url: URL of already-uploaded image file (may be temporary)
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
            # Check if image is temporary (in tmp/ folder) and move to permanent storage
            is_temporary = False
            if image_url:
                # Normalize path separators for cross-platform compatibility
                normalized_url = image_url.replace("\\", "/")
                
                # Check if URL contains tmp/ prefix (for both Supabase and local storage)
                # For local storage, check for double "tmp" pattern: /tmp/tmp/ (avoids false positives with STORAGE_LOCAL_PATH=./tmp)
                # For Supabase URLs, check for /tmp/documents/ pattern
                # For relative paths, check if it starts with tmp/
                if (
                    normalized_url.startswith("tmp/") or  # Relative path starting with tmp/
                    "/tmp/tmp/" in normalized_url or      # Local: ./tmp/tmp/documents/... (temporary)
                    ("/tmp/documents/" in normalized_url and normalized_url.startswith("http"))  # Supabase: .../tmp/documents/... (temporary)
                ):
                    is_temporary = True
                    logger.info(f"Detected temporary image, moving to permanent storage: {image_url}")
                    try:
                        permanent_url = StorageClient.move_from_temp(image_url)
                        logger.info(f"Successfully moved to permanent storage: {permanent_url}")
                        image_url = permanent_url
                    except Exception as e:
                        logger.error(f"Failed to move temporary image to permanent storage: {e}")
                        # Continue with temporary URL - better than failing completely
                        logger.warning(f"Continuing with temporary URL: {image_url}")
            
            # Verify user exists
            user = db.query(User).filter(User.id == owner_id).first()
            if not user:
                raise ValueError(f"User {owner_id} not found")
            
            # Normalize location_id: -1 means no location, convert to None
            normalized_location_id = None if location_id == -1 else location_id
            
            # 🎯 LOCATION RESOLUTION LOGIC:
            # If no location_id is provided (or it's -1) and we have an AI storage suggestion,
            # try to resolve an existing location by tag or name match (no auto-creation).
            if normalized_location_id is None and metadata and metadata.get("storage_suggestion"):
                suggestion = metadata.get("storage_suggestion")
                if suggestion and suggestion != "Other":
                    resolved_id = DocumentService._resolve_location(db, owner_id, suggestion)
                    if resolved_id:
                        normalized_location_id = resolved_id
                        logger.info(f"Resolved document location via suggestion '{suggestion}' -> ID: {resolved_id}")

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
        Create or update child Document records for each item in a receipt.
        
        Args:
            db: Database session
            receipt_doc: The parent receipt Document
            metadata: The extracted metadata containing 'items'
            
        Returns:
            List of item document IDs (created or updated)
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

            # Fetch existing child documents for this receipt
            existing_items_query = db.query(Document).filter(
                Document.owner_id == receipt_doc.owner_id
            ).all()
            
            # Map existing items by a unique key (original_text or title) to preserve them
            existing_items_map = {}
            for doc in existing_items_query:
                if doc.doc_metadata and doc.doc_metadata.get("source_receipt_id") == receipt_doc.id:
                    # Key: original_text if available, else title (product name)
                    key = doc.doc_metadata.get("original_text") or doc.title
                    if key:
                        existing_items_map[key] = doc

            item_ids = []
            processed_keys = set()

            # For each item, create or update document
            for item_data in items_data:
                item_name = item_data.get("product_name") or item_data.get("original_text") or "Unknown Item"
                original_text = item_data.get("original_text")
                category_code = item_data.get("category")
                
                # Determine match key
                match_key = original_text or item_name
                
                # Check if we have an existing document for this item
                item_doc = None
                # Prioritize matching by document_id if provided
                doc_id = item_data.get("document_id")
                if doc_id:
                    # Find doc in existing_items_query by ID
                    for doc in existing_items_query:
                        if doc.id == doc_id:
                            item_doc = doc
                            processed_keys.add(match_key) # Still mark the key as processed to avoid deletion if key matches
                            # Also mark key if it was found via ID, to prevent issues if key changed
                            if doc.title: processed_keys.add(doc.title)
                            if doc.doc_metadata and doc.doc_metadata.get("original_text"): processed_keys.add(doc.doc_metadata.get("original_text"))
                            break
                
                # Fallback to key matching if not found by ID
                if not item_doc and match_key in existing_items_map:
                    item_doc = existing_items_map[match_key]
                    processed_keys.add(match_key)
                
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
                
                # If explicit location_id provided in item_data (e.g. from UI update), use it
                if item_data.get("location_id"):
                     item_location_id = item_data.get("location_id")
                     # Also try to get name
                     loc = db.query(StorageLocation).filter(StorageLocation.id == item_location_id).first()
                     if loc:
                         item_location_name = loc.name
                elif storage_suggestion and storage_suggestion != "Other":
                    sub_category = item_data.get("sub_category")
                    resolved_id = DocumentService._resolve_location(
                        db, receipt_doc.owner_id, storage_suggestion, sub_category=sub_category
                    )
                    if resolved_id:
                        item_location_id = resolved_id
                        # Get the name for metadata update
                        matched_loc = db.query(StorageLocation).filter(StorageLocation.id == resolved_id).first()
                        item_location_name = matched_loc.name if matched_loc else storage_suggestion
                    # If resolved_id is None, item_location_id stays as receipt_doc.current_location_id (receipt's location)
                
                # Update item_data in the metadata list so it reflects the matched/created location
                if item_location_id:
                    item_data["location_id"] = item_location_id
                if item_location_name:
                    item_data["location_name"] = item_location_name

                # 3. Calculate expiry
                expiry_date_str = None
                # If explicit expiry date provided (e.g. from UI), use it
                if item_data.get("expiry_date"):
                    expiry_date_str = item_data.get("expiry_date")
                else:
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
                    "unit": item_data.get("unit"),
                    "purchase_date": purchase_date_str,
                    "expiry_date": expiry_date_str,
                    "status": "unopened",
                    "original_text": original_text,
                    "suggested_storage": storage_suggestion,
                    # sub_category is used by analyze_location to build top_sub_tags
                    "sub_category": item_data.get("sub_category"),
                }

                if item_doc:
                    # Update existing document
                    item_doc.title = item_name
                    item_doc.category_id = category_id
                    item_doc.current_location_id = item_location_id
                    # Merge metadata
                    current_meta = dict(item_doc.doc_metadata or {})
                    current_meta.update(item_doc_metadata)
                    item_doc.doc_metadata = current_meta
                    # Note: We do NOT touch embeddings here, preserving existing ones
                    
                    db.add(item_doc)
                    # Flush to ensure item_doc.id is available if needed (though it already should be)
                    db.flush()
                    item_ids.append(item_doc.id)
                    
                    # Update the item_data in the receipt metadata with the document_id
                    # This ensures the receipt knows exactly which document corresponds to this item
                    item_data["document_id"] = item_doc.id
                else:
                    # 5. Create new item document
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
                    
                    # Update the item_data in the receipt metadata with the document_id
                    item_data["document_id"] = item_doc.id

                    # 6. Generate embedding only for NEW items if passed
                    emb_raw = item_data.get("embedding")
                    if isinstance(emb_raw, list) and len(emb_raw) == 768:
                        try:
                            emb_floats = [float(x) for x in emb_raw]
                            db.add(DocumentEmbedding(document_id=item_doc.id, embedding=emb_floats))
                        except (ValueError, TypeError):
                            logger.warning("Item document_id=%s has invalid embedding, skipping document_embedding", item_doc.id)
            
            # Delete items that are no longer in the receipt
            for key, doc in existing_items_map.items():
                if key not in processed_keys:
                    db.delete(doc)

            # Save updated metadata with location info back to receipt document
            current_metadata = dict(receipt_doc.doc_metadata or {})
            current_metadata["items"] = items_data
            receipt_doc.doc_metadata = current_metadata
            db.add(receipt_doc)
            
            db.flush()
            items_count = len(item_ids)
            logger.info(f"Processed {items_count} item documents for receipt {receipt_doc.id} (ids: {item_ids})")

            # Refresh content_analysis for every distinct location that received items
            affected_location_ids = {
                item.get("location_id") for item in items_data if item.get("location_id")
            }
            logger.info(
                f"[Intelligence] Triggering analyze_location for {len(affected_location_ids)} location(s): "
                f"{affected_location_ids} | item sub_categories: "
                f"{[item.get('sub_category') for item in items_data]}"
            )
            for loc_id in affected_location_ids:
                DocumentService.analyze_location(db, loc_id)

            return item_ids
            
        except Exception as e:
            logger.error(f"Error creating/updating item documents: {e}", exc_info=True)
            # Non-critical, don't raise
            return 0
