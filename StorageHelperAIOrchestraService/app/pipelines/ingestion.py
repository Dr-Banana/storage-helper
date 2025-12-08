from typing import Dict, Any, Optional, Union, Callable, List
import logging
from dataclasses import dataclass, field

# Import module types
from app.modules.ocr import OCRResult, extract_text_advanced
from app.modules.cleaning import process_text
from app.modules.recommendation import generate_recommendation
from app.modules.embedding import EmbeddingGenerator
from app.modules.vision import VisionAnalyzer, VisionResult
# Storage logic removed - pipeline only processes, does not persist
from app.storage.output_schema import DocumentOutputSchema, default_output_schema
from app.core.config import settings
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    """State container for pipeline execution data."""
    image_url: str
    owner_id: int
    document_id: Optional[Union[int, str]] = None  # Can be int (from DB) or str (UUID from local storage)
    file_type: Optional[str] = None  # "image" or "pdf"
    
    # Processing results
    ocr_result: Optional[OCRResult] = None
    vision_result: Optional[VisionResult] = None  # Vision understanding result
    cleaned_text: Optional[str] = None
    cleaning_info: Optional[Dict[str, Any]] = None
    recommendation_result: Optional[Dict[str, Any]] = None
    embedding: Optional[list] = None
    embedding_status: str = "pending"
    
    # File storage
    file_url: Optional[str] = None  # URL of file stored in database (from upload-and-process API)
    file_upload_error: Optional[str] = None  # Error message if file upload failed
    
    # Pipeline metadata
    processing_steps: list = field(default_factory=list)
    status: str = "initialized"
    error: Optional[str] = None
    
    # Output schema for unified output management
    output_schema: DocumentOutputSchema = field(default_factory=lambda: default_output_schema)
    
    def to_output_dict(self, schema: Optional[DocumentOutputSchema] = None) -> Dict[str, Any]:
        """
        Convert pipeline state to output dictionary using output schema.
        
        :param schema: Optional output schema to use. If None, uses the instance's output_schema.
        :return: Output dictionary
        """
        schema = schema or self.output_schema
        
        # Prepare vision understanding data
        vision_understanding = None
        if self.vision_result:
            vision_understanding = {
                "description": self.vision_result.description,
                "confidence": self.vision_result.confidence,
                "detected_elements": self.vision_result.detected_elements,
                "has_text": self.vision_result.has_text,
            }
        
        # Prepare recommendation data
        recommendation_status = None
        recommendation_data = None
        recommendation_error = None
        if self.recommendation_result:
            recommendation_status = self.recommendation_result.get("status")
            if recommendation_status == "llm_success":
                recommendation_data = self.recommendation_result.get("recommendation")
            else:
                recommendation_error = self.recommendation_result.get("error")
        
        # Build output using schema
        return schema.build_output(
            status=self.status,
            owner_id=self.owner_id,
            source=self.image_url,
            file_type=self.file_type,
            document_id=self.document_id,
            processing_steps=self.processing_steps,
            file_url=self.file_url,
            file_upload_error=self.file_upload_error,
            extracted_text=self.cleaned_text or (self.ocr_result.text if self.ocr_result else None),
            ocr_confidence=self.ocr_result.confidence if self.ocr_result else None,
            raw_ocr_info=self.ocr_result.to_dict() if self.ocr_result else None,
            cleaning_info=self.cleaning_info,
            vision_understanding=vision_understanding,
            recommendation_status=recommendation_status,
            recommendation_data=recommendation_data,
            recommendation_error=recommendation_error,
            embedding=self.embedding,
            embedding_dimension=len(self.embedding) if self.embedding else None,
            embedding_status=self.embedding_status,  # Always include status, even if embedding failed
            error=self.error,
        )


class IngestionPipeline:
    """
    Modular ingestion pipeline for processing document images.
    
    This class orchestrates the document ingestion flow:
    OCR -> Recommendation -> Embedding -> Persistence
    
    All modules are injected via constructor, making the pipeline
    highly testable and configurable.
    """
    
    def __init__(
        self,
        ocr_extractor: Optional[Callable] = None,
        text_cleaner: Optional[Callable] = None,
        recommendation_generator: Optional[Callable] = None,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        vision_analyzer: Optional[VisionAnalyzer] = None,
    ):
        """
        Initialize the ingestion pipeline with module dependencies.
        
        :param ocr_extractor: Function/class for OCR text extraction. Defaults to extract_text_advanced.
        :param text_cleaner: Function for cleaning OCR text. Defaults to process_text from cleaning module.
        :param recommendation_generator: Function for generating recommendations. Defaults to generate_recommendation.
        :param embedding_generator: EmbeddingGenerator instance. Defaults to new EmbeddingGenerator().
        :param vision_analyzer: VisionAnalyzer instance for multimodal understanding. Defaults to new VisionAnalyzer().
        """
        # Set defaults for module dependencies
        self.ocr_extractor = ocr_extractor or extract_text_advanced
        self.text_cleaner = text_cleaner or process_text
        self.recommendation_generator = recommendation_generator or generate_recommendation
        self.embedding_generator = embedding_generator or EmbeddingGenerator()
        
        # Initialize vision analyzer with configuration
        vision_api_key = settings.VISION_API_KEY or settings.GEMINI_LLM_API_KEY
        self.vision_analyzer = vision_analyzer or VisionAnalyzer(
            api_key=vision_api_key,
            model_name=settings.VISION_MODEL,
            timeout=int(settings.VISION_TIMEOUT),
            enable_vision=settings.VISION_ENABLE
        )
        
        # Initialize pipeline storage for file uploads
        from app.storage.pipeline_storage import PipelineStorage
        self.pipeline_storage = PipelineStorage()
        
        logger.info(f"IngestionPipeline initialized with module dependencies (Vision: {'Enabled' if settings.VISION_ENABLE else 'Disabled'})")
    
    async def step_ocr(self, state: PipelineState) -> bool:
        """
        Step 1: Extract text from image using OCR.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        logger.info(f"STEP 1 (OCR): Processing image from {state.image_url}")
        
        try:
            # Run OCR extraction
            state.ocr_result = await self.ocr_extractor(state.image_url)
            
            # Check OCR result
            if not state.ocr_result or not state.ocr_result.text:
                state.status = "failed"
                state.error = "OCR Extraction Failed or returned empty text."
                logger.warning(f"OCR failed for {state.image_url}. Stopping pipeline.")
                return False
            
            state.processing_steps.append("OCR")
            confidence_str = f"{state.ocr_result.confidence:.2f}" if state.ocr_result.confidence else "N/A"
            logger.info(
                f"STEP 1 (OCR) Complete. Text length: {len(state.ocr_result.text)}, "
                f"Confidence: {confidence_str}"
            )
            state.status = "ocr_completed"
            return True
            
        except Exception as e:
            state.status = "failed"
            state.error = f"OCR step failed: {str(e)}"
            logger.error(f"STEP 1 (OCR) Failed: {e}", exc_info=True)
            return False
    
    async def step_vision_enhancement(self, state: PipelineState) -> bool:
        """
        Step 1B (Optional): Enhance understanding with Vision AI (multimodal).
        
        Triggers when:
        - Vision is globally enabled (VISION_ENABLE=True)
        - AND either:
          a) Auto-trigger is on AND OCR confidence is low, OR
          b) Auto-trigger is off (always run)
        
        Vision AI can understand:
        - Photos, logos, charts that OCR cannot read
        - Visual context and layout
        - Mixed text-image documents
        
        :param state: Pipeline state to update.
        :return: True if successful or skipped, False on critical error.
        """
        # Check if vision is enabled
        if not settings.VISION_ENABLE:
            logger.info("STEP 1B (Vision): Skipped (disabled in configuration)")
            return True
        
        # Check if we should trigger vision analysis
        should_trigger = True
        
        if settings.VISION_AUTO_TRIGGER_ON_LOW_OCR:
            # Only trigger if OCR confidence is low
            ocr_confidence = state.ocr_result.confidence if state.ocr_result else 0.0
            threshold = settings.VISION_OCR_CONFIDENCE_THRESHOLD
            
            if ocr_confidence >= threshold:
                logger.info(
                    f"STEP 1B (Vision): Skipped (OCR confidence {ocr_confidence:.2f} >= threshold {threshold})"
                )
                return True
            
            logger.info(
                f"STEP 1B (Vision): Triggered due to low OCR confidence "
                f"({ocr_confidence:.2f} < {threshold})"
            )
        else:
            logger.info("STEP 1B (Vision): Running for all documents (auto-trigger disabled)")
        
        try:
            # Run vision analysis
            logger.info(f"STEP 1B (Vision): Analyzing image with Gemini Vision API...")
            state.vision_result = await self.vision_analyzer.analyze_image(state.image_url)
            
            if state.vision_result and state.vision_result.description:
                state.processing_steps.append("Vision Enhancement")
                logger.info(
                    f"STEP 1B (Vision) Complete. Description length: {len(state.vision_result.description)}, "
                    f"Confidence: {state.vision_result.confidence:.2f}, "
                    f"Elements: {', '.join(state.vision_result.detected_elements)}"
                )
                
                # Merge vision description with OCR text for richer understanding
                if state.ocr_result and state.ocr_result.text:
                    # Combine OCR text with vision description
                    merged_text = f"""=== OCR Extracted Text ===
{state.ocr_result.text}

=== Visual Understanding (AI Analysis) ===
{state.vision_result.description}"""
                    
                    # Update OCR result with merged content
                    # This will be used by downstream steps (cleaning, recommendation, embedding)
                    state.ocr_result.text = merged_text
                    logger.info("Vision description merged with OCR text for enhanced understanding")
                
                state.status = "vision_completed"
                return True
            else:
                logger.warning("STEP 1B (Vision): No description returned, continuing without vision enhancement")
                return True
                
        except Exception as e:
            # Vision failure is not critical - continue pipeline with OCR only
            logger.error(f"STEP 1B (Vision) Failed: {e}", exc_info=True)
            logger.info("Continuing pipeline with OCR text only (graceful degradation)")
            return True  # Don't fail pipeline on vision errors
    
    async def step_cleaning(self, state: PipelineState) -> bool:
        """
        Step 2: Clean and normalize OCR text.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        if not state.ocr_result or not state.ocr_result.text:
            logger.error("Cannot clean text without OCR result.")
            return False
        
        logger.info("STEP 2 (Cleaning): Cleaning and normalizing OCR text...")
        
        try:
            # Clean the OCR text using injected text cleaner
            cleaning_info = await self.text_cleaner(
                ocr_text=state.ocr_result.text,
                ocr_data=state.ocr_result.to_dict().get("page_info"),
                min_confidence=0.0  # Don't filter by confidence, just clean
            )
            
            # Extract cleaned text from cleaning info
            cleaned_text = cleaning_info.get("cleaned_text", state.ocr_result.text)
            
            state.cleaned_text = cleaned_text
            state.cleaning_info = cleaning_info
            state.processing_steps.append("Cleaning")
            
            logger.info(
                f"STEP 2 (Cleaning) Complete. "
                f"Original length: {cleaning_info.get('original_length', 0)}, "
                f"Cleaned length: {cleaning_info.get('cleaned_length', 0)}"
            )
            state.status = "cleaning_completed"
            return True
            
        except Exception as e:
            state.status = "cleaning_failed"
            state.error = f"Cleaning step failed: {str(e)}"
            logger.error(f"STEP 2 (Cleaning) Failed: {e}", exc_info=True)
            # Don't fail the pipeline, just use original text
            state.cleaned_text = state.ocr_result.text
            return True  # Continue pipeline even if cleaning fails
    
    async def step_upload_file(self, state: PipelineState, page_number: int = 1) -> bool:
        """
        Step 2B: Upload document file to DataStorageService.
        
        This step uploads the file after cleaning is complete, so we can use
        the cleaned_text as ocr_text in the API request.
        
        :param state: Pipeline state to update.
        :param page_number: Page number within document (1-indexed).
        :return: True if successful or non-critical failure, False on critical error.
        """
        logger.info(f"STEP 2B (File Upload): Uploading document file to DataStorageService (page {page_number})...")
        
        try:
            # Get cleaned text from cleaning_info, fallback to cleaned_text or OCR text
            ocr_text = ""
            if state.cleaning_info and state.cleaning_info.get("cleaned_text"):
                ocr_text = state.cleaning_info.get("cleaned_text")
            elif state.cleaned_text:
                ocr_text = state.cleaned_text
            elif state.ocr_result and state.ocr_result.text:
                ocr_text = state.ocr_result.text
            else:
                logger.warning("No OCR text available for file upload")
                ocr_text = ""
            
            # Upload file with new API format
            upload_result = await self.pipeline_storage.upload_document_file(
                file_path=state.image_url,
                owner_id=state.owner_id,
                page_number=page_number,
                ocr_text=ocr_text,
                document_id=state.document_id  # Pass document_id (can be None)
            )
            
            if upload_result:
                # API returns document_id, not id
                returned_document_id = upload_result.get("document_id")
                state.file_url = upload_result.get("url") or upload_result.get("image_url")
                
                # Update document_id if we got one from the upload
                # For first page: use returned document_id if we didn't have one
                # For subsequent pages: should already have document_id, but update if returned
                if returned_document_id:
                    if not state.document_id:
                        # First page - use the returned document_id
                        state.document_id = returned_document_id
                        logger.info(f"Document ID established from upload: {state.document_id}")
                    elif state.document_id != returned_document_id:
                        # This shouldn't happen, but log a warning
                        logger.warning(f"Document ID mismatch: state has {state.document_id}, API returned {returned_document_id}")
                
                logger.info(f"File uploaded successfully. URL: {state.file_url}, Document ID: {state.document_id}")
                state.processing_steps.append("File Upload")
                return True
            else:
                # File upload failed but AI processing succeeded
                state.file_upload_error = "File upload to DataStorageService failed"
                logger.warning("File upload failed, but continuing with AI processing")
                return True  # Non-critical, continue pipeline
            
        except Exception as upload_error:
            # File upload failed but AI processing succeeded
            error_msg = str(upload_error)
            state.file_upload_error = f"File upload error: {error_msg}"
            logger.warning(f"File upload error (non-critical): {upload_error}")
            return True  # Non-critical, continue pipeline
    
    async def step_recommendation(self, state: PipelineState) -> bool:
        """
        Step 3: Generate storage recommendation using LLM.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        # Use cleaned text if available, otherwise fall back to OCR text
        text_to_use = state.cleaned_text or (state.ocr_result.text if state.ocr_result else None)
        
        if not text_to_use:
            logger.error("Cannot generate recommendation without text.")
            return False
        
        logger.info("STEP 3 (Recommendation): Generating structured storage recommendation using LLM...")
        
        try:
            state.recommendation_result = await self.recommendation_generator(
                document_text=text_to_use,
                owner_id=state.owner_id
            )
            
            state.processing_steps.append("Recommendation")
            
            if state.recommendation_result.get("status") == "llm_success":
                state.status = "llm_recommendation_completed"
                llm_data = state.recommendation_result.get("recommendation", {})
                suggested_location_id = llm_data.get("location_id")
                suggested_location_name = llm_data.get("location_name") or llm_data.get("suggested_location_name", "Unknown")
                category_code = llm_data.get("category_code", "Unknown")
                logger.info(
                    f"STEP 3 (Recommendation) Complete. "
                    f"Category: {category_code}, "
                    f"Location: {suggested_location_name} (ID: {suggested_location_id})"
                )
                return True
            else:
                state.status = "recommendation_failed"
                error_msg = state.recommendation_result.get("error", "Unknown LLM error.")
                logger.error(f"STEP 3 (Recommendation) Failed: {error_msg}")
                return False
                
        except Exception as e:
            state.status = "recommendation_failed"
            state.error = f"Recommendation step failed: {str(e)}"
            logger.error(f"STEP 3 (Recommendation) Failed: {e}", exc_info=True)
            return False
    
    async def step_embedding(self, state: PipelineState) -> bool:
        """
        Step 3: Generate vector embedding for semantic search.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        # Use cleaned text if available, otherwise fall back to OCR text
        text_to_use = state.cleaned_text or (state.ocr_result.text if state.ocr_result else None)
        
        if not text_to_use:
            logger.error("Cannot generate embedding without text.")
            return False
        
        logger.info("STEP 3 (Embedding): Generating document vector embedding...")
        
        try:
            state.embedding = await self.embedding_generator.generate(text_to_use)
            
            if state.embedding:
                state.embedding_status = "success"
                state.processing_steps.append("Embedding")
                logger.info(f"STEP 3 (Embedding) Complete. Vector dimension: {len(state.embedding)}")
                return True
            else:
                state.embedding_status = "failed"
                state.error = "Embedding generation failed, returned empty vector."
                logger.error("STEP 3 (Embedding) Failed: Empty vector returned.")
                return False
                
        except Exception as e:
            state.embedding_status = "failed"
            state.error = f"Embedding step failed: {str(e)}"
            logger.error(f"STEP 3 (Embedding) Failed: {e}", exc_info=True)
            return False
    
    async def step_persist(self, state: PipelineState, is_error: bool = False) -> bool:
        """
        Step 4: Generate document ID (storage logic removed).
        
        NOTE: Storage logic has been removed. This step only generates a document ID
        for the API response. Actual persistence should be handled by the API layer.
        
        :param state: Pipeline state to update.
        :param is_error: If True, indicates this is an error document
        :return: True if successful, False otherwise.
        """
        import uuid
        
        if is_error:
            logger.warning("STEP 4 (Persistence): Generating error document ID (storage disabled)...")
        else:
            logger.info("STEP 4 (Persistence): Generating document ID (storage disabled)...")
        
        try:
            # Generate document ID for API response (storage logic removed)
            doc_id = str(uuid.uuid4())
            state.document_id = doc_id
            state.processing_steps.append("Persistence")
            
            if is_error:
                # Preserve error status - do not overwrite with "completed"
                # The error status (e.g., "failed", "ocr_failed", "recommendation_failed") 
                # should be preserved to indicate pipeline failure
                logger.warning(f"⚠️  Error document ID generated: {doc_id}")
                logger.warning(f"    Error: {state.error}")
                logger.warning(f"    Failed step: {self._get_failed_step(state)}")
                logger.warning(f"    Status preserved: {state.status}")
            else:
                # Only set to "completed" if this is not an error document
                state.status = "completed"
                logger.info(f"STEP 4 (Persistence) Complete. Document ID: {doc_id} (storage disabled)")
            
            return True
                
        except Exception as e:
            state.status = "persistence_failed"
            state.error = f"Persistence step failed: {str(e)}"
            logger.error(f"STEP 4 (Persistence) Failed: {e}", exc_info=True)
            return False
    
    def _get_failed_step(self, state: PipelineState) -> str:
        """
        Determine which step failed based on pipeline state.
        
        :param state: Pipeline state
        :return: Name of the failed step
        """
        if state.status in ["failed", "ocr_failed"]:
            return "OCR"
        elif state.status == "cleaning_failed":
            return "Cleaning"
        elif state.status == "recommendation_failed":
            return "Recommendation"
        elif state.status == "embedding_failed":
            return "Embedding"
        elif state.status == "persistence_failed":
            return "Persistence"
        else:
            return "Unknown"
    
    async def run(
        self,
        image_url: str,
        owner_id: int,
        document_id: Optional[int] = None,
        skip_persist: bool = False,
        file_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute the complete ingestion pipeline.
        
        :param image_url: URL or path of the file to process (image or PDF).
        :param owner_id: ID of the user who owns the document.
        :param document_id: Optional existing document ID.
        :param skip_persist: If True, skip the persistence step.
        :param file_type: Type of file ("image" or "pdf"), auto-detected if not provided.
        :return: Dictionary containing the processed document data.
        """
        # Auto-detect file type if not provided
        if not file_type:
            from app.modules.ocr import detect_file_type
            file_type = detect_file_type(image_url)
        
        # Initialize pipeline state
        state = PipelineState(
            image_url=image_url,
            owner_id=owner_id,
            document_id=document_id,
            file_type=file_type
        )
        
        logger.info(f"Pipeline started for document_id={document_id}, processing {file_type} from: {image_url}")
        
        # Step 1: OCR
        if not await self.step_ocr(state):
            return state.to_output_dict()
        
        # Step 1B: Vision Enhancement (optional, based on configuration)
        # Enhances understanding with multimodal AI - can see photos, logos, charts beyond OCR
        await self.step_vision_enhancement(state)
        
        # Step 2: Cleaning (after OCR+Vision, before recommendation/embedding)
        await self.step_cleaning(state)
        
        # Step 2B: Upload file to DataStorageService (after cleaning, so we can use cleaned_text)
        await self.step_upload_file(state, page_number=1)
        
        # Step 3: Recommendation and Embedding (run in parallel)
        logger.info("STEP 3: Running Recommendation and Embedding in parallel...")
        
        # Run recommendation and embedding concurrently
        recommendation_task = asyncio.create_task(self.step_recommendation(state))
        embedding_task = asyncio.create_task(self.step_embedding(state))
        
        # Wait for both to complete (they can fail independently)
        recommendation_result, embedding_result = await asyncio.gather(
            recommendation_task,
            embedding_task,
            return_exceptions=True
        )
        
        # Log results
        if isinstance(recommendation_result, Exception):
            logger.error(f"Recommendation task failed: {recommendation_result}")
        if isinstance(embedding_result, Exception):
            logger.error(f"Embedding task failed: {embedding_result}")
        
        logger.info("STEP 3: Recommendation and Embedding completed (parallel execution)")
        
        # Step 4: Persistence
        if not skip_persist:
            # Check if pipeline failed at any critical step
            is_failed = state.status in [
                "failed",              # OCR failed
                "ocr_failed",          # OCR failed explicitly
                "recommendation_failed",  # Recommendation failed
                "embedding_failed"     # Embedding failed (optional but tracked)
            ]
            
            if is_failed:
                logger.error(f"Pipeline failed with status: {state.status}")
                logger.warning("Saving to error directory for debugging and potential retry...")
                # Save to error directory
                await self.step_persist(state, is_error=True)
            else:
                # Normal persistence
                await self.step_persist(state, is_error=False)
        
        logger.info(f"Pipeline completed with status: {state.status}")
        return state.to_output_dict()


# Default pipeline instance for backward compatibility
_default_pipeline = IngestionPipeline()


async def run_ingestion_pipeline(
    image_url: str,
    owner_id: int,
    document_id: Optional[int] = None,
    file_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Backward compatibility wrapper for run_ingestion_pipeline function.
    Uses the default IngestionPipeline instance.
    
    :param image_url: URL or path of the file to process (image or PDF).
    :param owner_id: ID of the user who owns the document.
    :param document_id: Optional existing document ID.
    :param file_type: Type of file ("image" or "pdf"), auto-detected if not provided.
    :return: Dictionary containing the processed document data.
    """
    return await _default_pipeline.run(image_url, owner_id, document_id, file_type=file_type)


async def run_batch_ingestion_pipeline(
    file_urls: List[str],
    owner_id: int,
    document_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Batch ingestion pipeline for processing multiple files (PDFs, images, or mixed).
    
    This function:
    1. Processes multiple files (PDFs are split into pages, images are treated as single pages)
    2. Assigns page numbers sequentially across all files
    3. Creates a document on the first page, uses the same document_id for subsequent pages
    4. Processes all pages in parallel (OCR + Cleaning + Upload)
    5. Runs Recommendation and Embedding on combined text from all pages
    
    :param file_urls: List of file URLs to process (images or PDFs)
    :param owner_id: ID of the user who owns the document
    :param document_id: Optional existing document ID
    :return: Dictionary containing batch processing results
    """
    from app.modules.ocr import detect_file_type
    from app.modules.pdf_processor import convert_pdf_to_images
    from pathlib import Path
    import tempfile
    import os
    from PIL import Image
    
    logger.info(f"Starting batch ingestion for {len(file_urls)} files, owner_id={owner_id}, document_id={document_id}")
    
    pipeline = _default_pipeline
    page_tasks = []  # List of (page_number, file_path, file_type) tuples
    temp_files = []  # Track temporary files for cleanup
    current_page = 1
    
    try:
        # Step 1: Process all files and split PDFs into pages
        for file_url in file_urls:
            file_type = detect_file_type(file_url)
            logger.info(f"Processing file: {file_url} (type: {file_type})")
            
            if file_type == "pdf":
                # Split PDF into pages
                try:
                    pdf_result = await convert_pdf_to_images(file_url, dpi=300)
                    logger.info(f"PDF split into {len(pdf_result.pages)} pages")
                    
                    # Save each page as a temporary image file
                    temp_dir = tempfile.mkdtemp(prefix="pdf_pages_")
                    temp_files.append(temp_dir)
                    
                    for page_data in pdf_result.pages:
                        page_num = page_data["page_number"]
                        page_image = page_data["image"]
                        
                        # Save page image to temporary file
                        temp_file_path = os.path.join(temp_dir, f"page_{page_num}.png")
                        page_image.save(temp_file_path, "PNG")
                        temp_files.append(temp_file_path)
                        
                        # Add to page tasks
                        page_tasks.append((current_page, temp_file_path, "image"))
                        current_page += 1
                        
                except Exception as e:
                    logger.error(f"Failed to split PDF {file_url}: {e}")
                    # Add as failed page
                    page_tasks.append((current_page, None, "pdf", str(e)))
                    current_page += 1
                    
            else:
                # Single image file - treat as one page
                page_tasks.append((current_page, file_url, "image"))
                current_page += 1
        
        if not page_tasks:
            logger.error("No pages to process after file splitting")
            return {
                "status": "failed",
                "document_id": None,
                "total_pages": 0,
                "successful_pages": 0,
                "failed_pages": 0,
                "page_results": [],
                "error": "No pages to process"
            }
        
        total_pages = len(page_tasks)
        logger.info(f"Total pages to process: {total_pages}")
        
        # Step 2: Establish document_id before processing remaining pages
        # Strategy: Process first page synchronously to get document_id, then process remaining pages in parallel
        # This ensures all pages use the same document_id, even if processing is interrupted
        all_cleaned_texts = []  # Collect text from all pages for recommendation/embedding
        page_results = []
        final_document_id = document_id
        
        async def process_single_page(page_info: tuple, use_document_id: Optional[int] = None) -> Dict[str, Any]:
            """Process a single page through the pipeline"""
            page_number, file_path, file_type_info = page_info[:3]
            error_msg = page_info[3] if len(page_info) > 3 else None
            
            if error_msg:
                return {
                    "page_number": page_number,
                    "status": "failed",
                    "error": error_msg,
                    "ocr_text": None,
                    "file_url": None
                }
            
            if not file_path:
                return {
                    "page_number": page_number,
                    "status": "failed",
                    "error": "File path is None",
                    "ocr_text": None,
                    "file_url": None
                }
            
            try:
                # Create pipeline state for this page
                state = PipelineState(
                    image_url=file_path,
                    owner_id=owner_id,
                    document_id=use_document_id,
                    file_type=file_type_info
                )
                
                # Step 1: OCR
                if not await pipeline.step_ocr(state):
                    return {
                        "page_number": page_number,
                        "status": "failed",
                        "error": state.error or "OCR failed",
                        "ocr_text": None,
                        "file_url": None
                    }
                
                # Step 1B: Vision Enhancement (optional)
                await pipeline.step_vision_enhancement(state)
                
                # Step 2: Cleaning
                await pipeline.step_cleaning(state)
                
                # Collect cleaned text for recommendation/embedding
                cleaned_text = state.cleaned_text or (state.ocr_result.text if state.ocr_result else "")
                if cleaned_text:
                    all_cleaned_texts.append((page_number, cleaned_text))
                
                # Step 2B: Upload file (use page_number from task)
                upload_success = await pipeline.step_upload_file(state, page_number=page_number)
                
                return {
                    "page_number": page_number,
                    "status": "success" if upload_success else "failed",
                    "error": state.file_upload_error if not upload_success else None,
                    "ocr_text": cleaned_text,
                    "file_url": state.file_url,
                    "document_id": state.document_id  # Return document_id for first page
                }
                
            except Exception as e:
                logger.error(f"Error processing page {page_number}: {e}", exc_info=True)
                return {
                    "page_number": page_number,
                    "status": "failed",
                    "error": str(e),
                    "ocr_text": None,
                    "file_url": None,
                    "document_id": None
                }
        
        # Step 2A: Process first page synchronously to establish document_id
        # This ensures document_id is available before processing remaining pages
        if page_tasks:
            first_page_info = page_tasks[0]
            logger.info(f"Processing first page synchronously to establish document_id (current: {final_document_id})...")
            first_page_result = await process_single_page(first_page_info, use_document_id=final_document_id)
            page_results.append(first_page_result)
            
            # Extract document_id from first page result
            # This is critical - all subsequent pages will use this document_id
            first_page_doc_id = first_page_result.get("document_id")
            if first_page_doc_id:
                final_document_id = first_page_doc_id
                logger.info(f"Document ID established from first page: {final_document_id}. All remaining pages will use this document_id.")
            else:
                # First page didn't return document_id - this is a problem
                if first_page_result.get("status") == "failed":
                    logger.error(f"First page failed and no document_id was created: {first_page_result.get('error')}")
                    logger.warning("Subsequent pages may create separate documents if processing continues.")
                else:
                    logger.error("First page processed but no document_id returned. This should not happen.")
                    logger.warning("Subsequent pages may create separate documents.")
        
        # Step 2B: Process remaining pages in parallel (if any)
        # All pages now use the established document_id from first page
        if len(page_tasks) > 1:
            remaining_pages = page_tasks[1:]
            logger.info(f"Processing remaining {len(remaining_pages)} pages in parallel...")
            remaining_tasks = [
                process_single_page(page_info, use_document_id=final_document_id) 
                for page_info in remaining_pages
            ]
            remaining_results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
            
            # Handle exceptions in remaining results
            for i, result in enumerate(remaining_results):
                if isinstance(result, Exception):
                    page_num = remaining_pages[i][0]
                    page_results.append({
                        "page_number": page_num,
                        "status": "failed",
                        "error": str(result),
                        "ocr_text": None,
                        "file_url": None,
                        "document_id": None
                    })
                else:
                    page_results.append(result)
        
        # Sort results by page number
        page_results.sort(key=lambda x: x["page_number"])
        processed_results = page_results
        
        # Count successes and failures
        successful_pages = sum(1 for r in processed_results if r["status"] == "success")
        failed_pages = total_pages - successful_pages
        
        # Step 3: Run Recommendation and Embedding on combined text from all pages
        recommendation_result = None
        embedding_result = None
        
        if successful_pages > 0 and all_cleaned_texts:
            # Combine text from all pages
            combined_text = "\n\n".join([f"[Page {pnum}]\n{text}" for pnum, text in sorted(all_cleaned_texts)])
            
            logger.info(f"Running Recommendation and Embedding on combined text ({len(combined_text)} chars)...")
            
            # Run recommendation and embedding in parallel
            recommendation_task = asyncio.create_task(
                pipeline.recommendation_generator(combined_text, owner_id)
            )
            embedding_task = asyncio.create_task(
                pipeline.embedding_generator.generate(combined_text)
            )
            
            recommendation_result, embedding_result = await asyncio.gather(
                recommendation_task,
                embedding_task,
                return_exceptions=True
            )
            
            if isinstance(recommendation_result, Exception):
                logger.error(f"Recommendation failed: {recommendation_result}")
                recommendation_result = None
            if isinstance(embedding_result, Exception):
                logger.error(f"Embedding failed: {embedding_result}")
                embedding_result = None
        
        # Build response
        # Convert document_id to int if it's a string (from API response)
        document_id_int = None
        if final_document_id:
            if isinstance(final_document_id, str):
                try:
                    document_id_int = int(final_document_id)
                except (ValueError, TypeError):
                    logger.warning(f"Could not convert document_id '{final_document_id}' to int")
                    document_id_int = None
            elif isinstance(final_document_id, int):
                document_id_int = final_document_id
            else:
                logger.warning(f"Unexpected document_id type: {type(final_document_id)}")
                document_id_int = None
        
        response = {
            "status": "success" if failed_pages == 0 else ("partial_success" if successful_pages > 0 else "failed"),
            "document_id": document_id_int,
            "total_pages": total_pages,
            "successful_pages": successful_pages,
            "failed_pages": failed_pages,
            "page_results": processed_results
        }
        
        # Add recommendation data if available
        if recommendation_result and recommendation_result.get("status") == "llm_success":
            rec_data = recommendation_result.get("recommendation", {}).copy()
            
            # Normalize location fields: use location_id instead of location_name
            # Prefer location_id over suggested_location_id
            location_id = rec_data.get("location_id") or rec_data.get("suggested_location_id")
            if location_id:
                rec_data["location_id"] = location_id
            
            # Always remove location_name fields (use ID only)
            rec_data.pop("location_name", None)
            rec_data.pop("suggested_location_name", None)
            
            # Remove suggested_location_id if location_id is set (avoid duplication)
            if "location_id" in rec_data and "suggested_location_id" in rec_data:
                rec_data.pop("suggested_location_id", None)
            
            # Remove category_code (use category_id only to save space)
            rec_data.pop("category_code", None)
            
            response["recommendation"] = rec_data
        
        # Add embedding info if available
        if embedding_result:
            response["embedding"] = embedding_result
            response["embedding_dimension"] = len(embedding_result) if embedding_result else None
        
        logger.info(f"Batch ingestion complete: {successful_pages}/{total_pages} pages successful, document_id={final_document_id if final_document_id else 'None'}")
        return response
        
    finally:
        # Clean up temporary files
        for temp_file in temp_files:
            try:
                if os.path.isfile(temp_file):
                    os.remove(temp_file)
                elif os.path.isdir(temp_file):
                    import shutil
                    shutil.rmtree(temp_file)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file}: {e}")
