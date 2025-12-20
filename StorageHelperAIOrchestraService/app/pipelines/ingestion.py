from typing import Dict, Any, Optional, Union, Callable, List
import logging
from dataclasses import dataclass, field

# Import module types
from app.modules.ocr import OCRResult, extract_text_advanced
from app.modules.cleaning import process_text
from app.modules.recommendation import generate_recommendation
from app.modules.embedding import EmbeddingGenerator, EmbeddingResult
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
    document_id: Optional[Union[int, str]] = None  # Document ID from backend API (int from DB)
    file_type: Optional[str] = None  # "image" or "pdf"
    
    # Processing results
    ocr_result: Optional[OCRResult] = None
    vision_result: Optional[VisionResult] = None  # Vision understanding result
    cleaned_text: Optional[str] = None
    cleaning_info: Optional[Dict[str, Any]] = None
    recommendation_result: Optional[Dict[str, Any]] = None
    embedding_result: Optional[EmbeddingResult] = None
    
    # File storage
    file_url: Optional[str] = None  # URL of file stored in database (from upload/process API)
    page_id: Optional[int] = None  # Page ID from database (from upload/process API)
    processed_page_number: Optional[int] = None  # Page number returned by /documents/process
    file_upload_error: Optional[str] = None  # Error message if file upload failed
    embedding_save_error: Optional[str] = None  # Error message if embedding save failed
    
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
        
        # Prepare embedding data
        embedding = None
        embedding_dimension = None
        embedding_status = None
        if self.embedding_result:
            embedding = self.embedding_result.vector if self.embedding_result.is_successful else None
            embedding_dimension = self.embedding_result.dimension if self.embedding_result.is_successful else None
            embedding_status = self.embedding_result.status
        
        # Build output using schema
        output_dict = schema.build_output(
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
            embedding=embedding,
            embedding_dimension=embedding_dimension,
            embedding_status=embedding_status,  # Always include status, even if embedding failed
            error=self.error,
        )
        
        # Add embedding_save_error if present (not in schema yet, add directly)
        if self.embedding_save_error:
            output_dict["embedding_save_error"] = self.embedding_save_error
        
        return output_dict


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
        Step 2B: Upload document file to DataStorageService and get image_url.
        
        This step uploads the file to get image_url. The actual document processing
        (saving metadata) happens later in step_process_document after pipeline completes.
        
        :param state: Pipeline state to update.
        :param page_number: Page number within document (1-indexed).
        :return: True if upload succeeded, False if upload failed (pipeline continues regardless).
        """
        logger.info(f"STEP 2B (File Upload): Uploading document file to DataStorageService (page {page_number})...")
        
        try:
            # Upload file only to get image_url
            image_url = await self.pipeline_storage.upload_file_only(
                file_path=state.image_url,
                owner_id=state.owner_id
            )
            
            if image_url:
                # Store image_url for later use in process step
                state.file_url = image_url
                logger.info(f"File uploaded successfully. Image URL: {state.file_url}")
                state.processing_steps.append("File Upload")
                return True
            else:
                # File upload failed but AI processing can still continue
                state.file_upload_error = "File upload to DataStorageService failed"
                logger.warning("File upload failed, but continuing with AI processing")
                # 返回 False，让调用方知道这个页面的存储相关信息不可用
                return False
            
        except Exception as upload_error:
            # File upload failed but AI processing can still continue
            error_msg = str(upload_error)
            state.file_upload_error = f"File upload error: {error_msg}"
            logger.warning(f"File upload error (non-critical): {upload_error}")
            # 返回 False，让调用方知道上传失败
            return False
    
    async def step_process_document(self, state: PipelineState, page_number: int = 1) -> bool:
        """
        Step 4B: Process document page metadata via DataStorageService API.
        
        This step processes the document page metadata AFTER the entire pipeline
        (OCR, Vision, Cleaning, Recommendation, Embedding) is complete.
        It sends the structured results and file storage path to /documents/process API.
        
        :param state: Pipeline state to update.
        :param page_number: Page number within document (1-indexed).
        :return: True if successful or non-critical failure, False on critical error.
        """
        logger.info(f"STEP 4B (Process Document): Processing document page metadata (page {page_number})...")
        
        # Check if we have image_url from upload step
        if not state.file_url:
            state.file_upload_error = "No image_url available for document processing (upload step may have failed)"
            logger.warning("Cannot process document: no image_url available")
            return True  # Non-critical, continue pipeline
        
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
                logger.warning("No OCR text available for document processing")
                ocr_text = ""
            
            # Extract category_id and location_id from recommendation_result if available
            category_id = None
            location_id = None
            if state.recommendation_result and state.recommendation_result.get("status") == "llm_success":
                rec_data = state.recommendation_result.get("recommendation", {})
                category_id = rec_data.get("category_id")
                # Get location_id, prefer location_id over suggested_location_id
                location_id = rec_data.get("location_id") or rec_data.get("suggested_location_id")
                # Normalize: None or -1 means no location
                if location_id is None:
                    location_id = -1
            
            # Process document page with image_url and structured results
            process_result = await self.pipeline_storage.process_document_page(
                image_url=state.file_url,
                owner_id=state.owner_id,
                page_number=page_number,
                ocr_text=ocr_text,
                document_id=state.document_id,  # Pass document_id (can be None)
                category_id=category_id,
                location_id=location_id
            )
            
            if process_result:
                # API returns document_id, not id
                returned_document_id = process_result.get("document_id")
                returned_page_id = process_result.get("page_id")
                state.page_id = returned_page_id  # Store page_id from API response

                # Update file_url and processed_page_number from API response
                returned_image_url = process_result.get("image_url")
                if returned_image_url:
                    state.file_url = returned_image_url

                returned_page_number = process_result.get("page_number")
                if returned_page_number is not None:
                    state.processed_page_number = returned_page_number
                
                # Update document_id if we got one from the process step
                # For first page: use returned document_id if we didn't have one
                # For subsequent pages: should already have document_id, but update if returned
                if returned_document_id:
                    if not state.document_id:
                        # First page - use the returned document_id
                        state.document_id = returned_document_id
                        logger.info(f"Document ID established from process: {state.document_id}")
                    elif state.document_id != returned_document_id:
                        # This shouldn't happen, but log a warning
                        logger.warning(f"Document ID mismatch: state has {state.document_id}, API returned {returned_document_id}")
                
                logger.info(f"Document page processed successfully. Document ID: {state.document_id}, Page ID: {state.page_id}")
                state.processing_steps.append("Process Document")
                return True
            else:
                # Document processing failed but AI processing succeeded
                state.file_upload_error = "Document processing via DataStorageService API failed"
                logger.warning("Document processing failed, but AI processing completed")
                return True  # Non-critical, continue pipeline
            
        except Exception as process_error:
            # Document processing failed but AI processing succeeded
            error_msg = str(process_error)
            state.file_upload_error = f"Document processing error: {error_msg}"
            logger.warning(f"Document processing error (non-critical): {process_error}")
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
        Step 3: Generate vector embedding for document representation.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        # Use cleaned text if available, otherwise fall back to OCR text
        text_to_use = state.cleaned_text or (state.ocr_result.text if state.ocr_result else None)
        
        if not text_to_use:
            logger.error("Cannot generate embedding without text.")
            state.embedding_result = EmbeddingResult.create_failed(
                error="No text available for embedding generation"
            )
            state.status = "embedding_failed"
            return False
        
        logger.info("STEP 3 (Embedding): Generating document vector embedding...")
        
        try:
            state.embedding_result = await self.embedding_generator.generate(text_to_use)
            
            if state.embedding_result.is_successful:
                state.processing_steps.append("Embedding")
                logger.info(f"STEP 3 (Embedding) Complete. Vector dimension: {state.embedding_result.dimension}")
                return True
            else:
                state.status = "embedding_failed"
                state.error = state.embedding_result.error or "Embedding generation failed, returned empty vector."
                logger.error(f"STEP 3 (Embedding) Failed: {state.error}")
                return False
                
        except Exception as e:
            state.status = "embedding_failed"
            state.embedding_result = EmbeddingResult.create_failed(
                error=f"Embedding step failed: {str(e)}",
                model_name=self.embedding_generator.model_name,
                task_type=self.embedding_generator.task_type
            )
            state.error = f"Embedding step failed: {str(e)}"
            logger.error(f"STEP 3 (Embedding) Failed: {e}", exc_info=True)
            return False
    
    async def step_persist(self, state: PipelineState, is_error: bool = False) -> bool:
        """
        Step 4: Use document ID from upload API (storage logic removed).
        
        NOTE: Storage logic has been removed. This step uses the document_id
        returned from /api/v1/documents/process API (after upload step). All document_id
        management is handled by the backend. If document_id is not available
        (e.g., upload failed), it remains None.
        
        :param state: Pipeline state to update.
        :param is_error: If True, indicates this is an error document
        :return: True if successful, False otherwise.
        """
        try:
            # Use document_id from upload API if available
            # This is the document_id returned by /api/v1/documents/process (after upload step)
            # All document_id management is handled by the backend - we don't generate any IDs
            doc_id = state.document_id
            if doc_id:
                if is_error:
                    logger.warning(f"STEP 4 (Persistence): Using document ID from upload API: {doc_id} (error document)")
                else:
                    logger.info(f"STEP 4 (Persistence): Using document ID from upload API: {doc_id}")
            else:
                # No document_id available (upload may have failed)
                if is_error:
                    logger.warning(f"STEP 4 (Persistence): No document ID available (upload failed or not executed)")
                else:
                    logger.warning(f"STEP 4 (Persistence): No document ID available (upload may have failed)")
            
            state.processing_steps.append("Persistence")
            
            if is_error:
                # Preserve error status - do not overwrite with "completed"
                # The error status (e.g., "failed", "ocr_failed", "recommendation_failed") 
                # should be preserved to indicate pipeline failure
                logger.warning(f"⚠️  Error document processing complete")
                logger.warning(f"    Document ID: {doc_id if doc_id else 'None (upload failed)'}")
                logger.warning(f"    Error: {state.error}")
                logger.warning(f"    Failed step: {self._get_failed_step(state)}")
                logger.warning(f"    Status preserved: {state.status}")
            else:
                # Only set to "completed" if this is not an error document
                state.status = "completed"
                if doc_id:
                    logger.info(f"STEP 4 (Persistence) Complete. Document ID: {doc_id}")
                else:
                    logger.warning(f"STEP 4 (Persistence) Complete. No document ID (upload may have failed)")
            
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
        
        # Step 3C: Save embedding to DataStorageService via API
        # This should be done after pages are processed (document_id is available)
        # Only track embedding_save_error when a save operation is actually attempted and fails
        # Do not set error when save wasn't attempted due to missing preconditions
        if state.document_id and state.embedding_result and state.embedding_result.is_successful:
            # All preconditions met - attempt to save embedding
            try:
                # Convert document_id to int if needed
                doc_id = state.document_id
                if isinstance(doc_id, str):
                    try:
                        doc_id = int(doc_id)
                    except (ValueError, TypeError):
                        # Precondition check failed - cannot convert document_id
                        # Don't set embedding_save_error as no save was attempted
                        logger.warning(f"Could not convert document_id '{doc_id}' to int for embedding save - skipping save")
                        doc_id = None
                
                if doc_id and isinstance(doc_id, int):
                    embedding_vector = state.embedding_result.vector
                    if embedding_vector and len(embedding_vector) == 768:
                        # Actually attempt to save - this is where we track failures
                        save_success = await self.pipeline_storage.save_document_embedding(
                            document_id=doc_id,
                            embedding=embedding_vector
                        )
                        if save_success:
                            logger.info(f"Document embedding saved successfully via API for document_id={doc_id}")
                        else:
                            # Save was attempted but failed - track this as an error
                            error_msg = f"Failed to save document embedding via API for document_id={doc_id}"
                            logger.warning(error_msg)
                            state.embedding_save_error = error_msg
                    else:
                        # Precondition check failed - dimension mismatch
                        # Don't set embedding_save_error as no save was attempted
                        logger.warning(f"Embedding vector dimension mismatch: expected 768, got {len(embedding_vector) if embedding_vector else 0} - skipping save")
                else:
                    # Precondition check failed - invalid document_id
                    # Don't set embedding_save_error as no save was attempted
                    logger.warning(f"Invalid document_id for embedding save: {state.document_id} - skipping save")
            except Exception as e:
                # Exception during save attempt - track as error
                # This exception occurred during the save attempt, so it's a real failure
                error_msg = f"Error saving document embedding via API: {str(e)}"
                logger.error(error_msg, exc_info=True)
                state.embedding_save_error = error_msg
                # Don't fail the pipeline if embedding save fails, but track the error
        else:
            # Save wasn't attempted due to missing preconditions - don't set embedding_save_error
            # These are expected conditions, not errors
            if not state.document_id:
                logger.info("Skipping embedding save: document_id not available (this is expected for failed page processing)")
            elif not state.embedding_result or not state.embedding_result.is_successful:
                logger.info("Skipping embedding save: embedding generation failed or not successful (this is expected when embedding generation fails)")
        
        # Step 4B: Process document page metadata (after all pipeline processing is complete)
        # This sends structured results and file storage path to /documents/process API
        await self.step_process_document(state, page_number=1)
        
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


async def run_unified_ingestion_pipeline(
    file_urls: List[str],
    owner_id: int,
    document_id: Optional[int] = None,
    file_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Unified ingestion pipeline for processing files (single or multiple PDFs, images, or mixed).
    
    This function handles both single file and batch processing:
    1. Processes files (PDFs are split into pages, images are treated as single pages)
    2. Assigns page numbers sequentially across all files
    3. Creates a document on the first page, uses the same document_id for subsequent pages
    4. Processes all pages in parallel (OCR + Cleaning + Upload)
    5. Runs Recommendation and Embedding on combined text from all pages
    
    :param file_urls: List of file URLs to process (images or PDFs). Can be single file [url] or multiple files [url1, url2, ...]
    :param owner_id: ID of the user who owns the document
    :param document_id: Optional existing document ID
    :param file_type: Optional file type override ("image" or "pdf"). Only used for single file uploads to override auto-detection.
    :return: Dictionary containing processing results with total_pages, successful_pages, failed_pages, page_results
    """
    from app.modules.ocr import detect_file_type
    from app.modules.pdf_processor import convert_pdf_to_images
    from pathlib import Path
    import tempfile
    import os
    from PIL import Image
    
    logger.info(f"Starting unified ingestion for {len(file_urls)} files, owner_id={owner_id}, document_id={document_id}")
    
    pipeline = _default_pipeline
    # page_tasks: List of tuples
    # (global_page_index, ocr_source_path, file_type, file_image_url, page_number_within_file)
    page_tasks = []
    temp_files = []  # Track temporary files for cleanup
    current_page_global = 1  # 全局页序，只用于内部排序 / 映射
    
    try:
        # Step 1: Process all files and split PDFs into pages
        for idx, file_url in enumerate(file_urls):
            # Use provided file_type for single file uploads, otherwise auto-detect
            if len(file_urls) == 1 and file_type:
                # Single file with explicit file_type override
                detected_type = file_type
                logger.info(f"Processing file: {file_url} (type: {detected_type} - user specified)")
            else:
                # Auto-detect file type (for multiple files or when file_type not provided)
                detected_type = detect_file_type(file_url)
                logger.info(f"Processing file: {file_url} (type: {detected_type} - auto-detected)")
            
            file_type_for_task = detected_type

            # 🆕 上传「原始文件」一次，所有页面共享同一个 image_url
            file_image_url: Optional[str] = None
            upload_error: Optional[str] = None
            try:
                logger.info(f"Uploading original file for unified ingestion: {file_url}")
                file_image_url = await pipeline.pipeline_storage.upload_file_only(
                    file_path=file_url,
                    owner_id=owner_id
                )
                if not file_image_url:
                    upload_error = "File upload to DataStorageService failed"
                    logger.warning(f"{upload_error} (file: {file_url})")
            except Exception as e:
                upload_error = f"File upload error: {str(e)}"
                logger.warning(f"{upload_error} (file: {file_url})")

            # 对当前文件内的页面使用「文件内页码」(1..N)
            page_number_within_file = 1
            
            if detected_type == "pdf":
                # Split PDF into pages（仅用于 OCR / AI，不再单页上传）
                try:
                    pdf_result = await convert_pdf_to_images(file_url, dpi=300)
                    logger.info(f"PDF split into {len(pdf_result.pages)} pages")
                    
                    # Save each page as a temporary image file
                    temp_dir = tempfile.mkdtemp(prefix="pdf_pages_")
                    temp_files.append(temp_dir)
                    
                    for page_data in pdf_result.pages:
                        page_num = page_data["page_number"]
                        page_image = page_data["image"]
                        
                        # Save page image to temporary file（OCR 用）
                        temp_file_path = os.path.join(temp_dir, f"page_{page_num}.png")
                        page_image.save(temp_file_path, "PNG")
                        temp_files.append(temp_file_path)
                        
                        # Add to page tasks
                        page_tasks.append(
                            (
                                current_page_global,   # 全局索引
                                temp_file_path,        # OCR 源文件（单页图片）
                                "image",               # OCR 文件类型
                                file_image_url,        # 此原始文件的 image_url（整文件上传得到）
                                page_number_within_file,  # 当前文件内的页码
                                upload_error           # 上传是否失败的信息
                            )
                        )
                        current_page_global += 1
                        page_number_within_file += 1
                        
                except Exception as e:
                    logger.error(f"Failed to split PDF {file_url}: {e}")
                    # Add as failed page（仍然保留一条记录，方便前端看到错误）
                    page_tasks.append(
                        (
                            current_page_global,
                            None,
                            "pdf",
                            file_image_url,
                            page_number_within_file,
                            str(e)
                        )
                    )
                    current_page_global += 1
                    page_number_within_file += 1
                    
            else:
                # 单张图片文件：视为单页，page_number = 1
                page_tasks.append(
                    (
                        current_page_global,
                        file_url,           # OCR 源文件（整张图）
                        "image",
                        file_image_url,     # 整个文件上传得到的 image_url
                        page_number_within_file,
                        upload_error
                    )
                )
                current_page_global += 1
        
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
        page_states = {}  # Store state for each page: {page_number: PipelineState}
        final_document_id = document_id
        first_page_global_index = None  # Track first page's global_index to skip it in Step 4B
        
        async def process_single_page(page_info: tuple, use_document_id: Optional[int] = None) -> tuple[Dict[str, Any], Optional[PipelineState]]:
            """Process a single page through the pipeline. Returns (result_dict, state)"""
            (
                global_page_index,
                file_path,
                file_type_info,
                file_image_url,
                page_number_within_file,
                error_msg
            ) = page_info
            
            if error_msg:
                return ({
                    "page_number": page_number_within_file,
                    "global_page_index": global_page_index,
                    "status": "failed",
                    "error": error_msg,
                    "ocr_text": None,
                    "file_url": None,
                    "page_id": None
                }, None)
            
            if not file_path:
                return ({
                    "page_number": page_number_within_file,
                    "global_page_index": global_page_index,
                    "status": "failed",
                    "error": "File path is None",
                    "ocr_text": None,
                    "file_url": None,
                    "page_id": None
                }, None)
            
            try:
                # Create pipeline state for this page
                state = PipelineState(
                    image_url=file_path,
                    owner_id=owner_id,
                    document_id=use_document_id,
                    file_type=file_type_info
                )
                # 🆕 对于统一上传模式，每一页都共享原始文件的 image_url
                state.file_url = file_image_url
                
                # Step 1: OCR
                if not await pipeline.step_ocr(state):
                    return ({
                        "page_number": page_number_within_file,
                        "global_page_index": global_page_index,
                        "status": "failed",
                        "error": state.error or "OCR failed",
                        "ocr_text": None,
                        "file_url": None,
                        "document_id": None,
                        "page_id": None
                    }, state)
                
                # Step 1B: Vision Enhancement (optional)
                await pipeline.step_vision_enhancement(state)
                
                # Step 2: Cleaning
                await pipeline.step_cleaning(state)
                
                # Collect cleaned text for recommendation/embedding
                cleaned_text = state.cleaned_text or (state.ocr_result.text if state.ocr_result else "")
                if cleaned_text:
                    # 这里用「文件内页码」作为标记，后续组合文本时会按页码排序
                    all_cleaned_texts.append((page_number_within_file, cleaned_text))

                # 统一上传模式下，这里不再重复上传；如果文件级上传失败，则标记为 failed
                if not file_image_url:
                    return ({
                        "page_number": page_number_within_file,
                        "global_page_index": global_page_index,
                        "status": "failed",
                        "error": error_msg or "File upload to DataStorageService failed",
                        "ocr_text": cleaned_text,
                        "file_url": None,
                        "document_id": None,
                        "page_id": None
                    }, state)

                return ({
                    "page_number": page_number_within_file,  # 文件内页码（用于显示）
                    "global_page_number": global_page_index,  # 全局页码（用于数据库，确保唯一性）
                    "global_page_index": global_page_index,
                    "status": "success",
                    "error": None,
                    "ocr_text": cleaned_text,
                    "file_url": state.file_url,  # 与原始文件共享的 image_url
                    "document_id": state.document_id,  # Return document_id for first page
                    "page_id": state.page_id  # Will be set after process step
                }, state)
                
            except Exception as e:
                logger.error(f"Error processing page (file_page={page_number_within_file}, global_index={global_page_index}): {e}", exc_info=True)
                return ({
                    "page_number": page_number_within_file,
                    "global_page_index": global_page_index,
                    "status": "failed",
                    "error": str(e),
                    "ocr_text": None,
                    "file_url": None,
                    "document_id": None,
                    "page_id": None
                }, None)
        
        # Step 2A: Process first page synchronously to establish document_id
        # This ensures document_id is available before processing remaining pages
        # IMPORTANT: For single-page documents, we defer processing until after recommendation
        # For multi-page documents, we process first page immediately to establish document_id
        total_pages = len(page_tasks)
        is_single_page = total_pages == 1
        
        if page_tasks:
            first_page_info = page_tasks[0]
            logger.info(f"Processing first page synchronously to establish document_id (current: {final_document_id}, total_pages: {total_pages}, is_single_page: {is_single_page})...")
            first_page_result, first_page_state = await process_single_page(first_page_info, use_document_id=final_document_id)
            page_results.append(first_page_result)
            first_page_state_global_index = None  # Store for later use in Step 3B
            if first_page_state:
                first_page_state_global_index = first_page_result.get("global_page_index")
                page_states[first_page_state_global_index] = first_page_state
                
                # For multi-page documents: immediately process first page to get document_id
                # For single-page documents: defer processing until after recommendation (in Step 3B)
                if first_page_result.get("status") == "success":
                    if not is_single_page:
                        # Multi-page: Process first page immediately to establish document_id
                        # Use global_page_number for database (ensures uniqueness across all files)
                        # Fallback to page_number if global_page_number is not available
                        global_page_num = first_page_result.get("global_page_number") or first_page_result.get("page_number", 1)
                        # Process first page to establish document_id
                        logger.info(f"Processing first page to establish document_id (multi-page document, global_page_number={global_page_num}, global_index={first_page_result.get('global_page_index')})...")
                        await pipeline.step_process_document(first_page_state, page_number=global_page_num)
                        
                        # Update final_document_id from first page's result
                        if first_page_state.document_id is not None:
                            final_document_id = first_page_state.document_id
                            logger.info(f"Document ID established from first page: {final_document_id}")
                        
                        # Update first_page_result with values from step_process_document
                        # This is critical: we mark the first page as processed by setting page_id
                        if first_page_state.page_id is not None:
                            first_page_result["page_id"] = first_page_state.page_id
                            logger.info(f"First page processed successfully: page_id={first_page_state.page_id}, document_id={first_page_state.document_id}, global_page_number={global_page_num}")
                        else:
                            logger.warning(f"First page processing did not return page_id (global_page_number={global_page_num}) - this may cause duplicate insertion!")
                        
                        if first_page_state.document_id is not None:
                            first_page_result["document_id"] = first_page_state.document_id
                        if first_page_state.file_url is not None:
                            first_page_result["file_url"] = first_page_state.file_url
                        if first_page_state.processed_page_number is not None:
                            first_page_result["page_number"] = first_page_state.processed_page_number
                    else:
                        # Single-page: Defer processing until after recommendation
                        logger.info(f"Single-page document detected - deferring first page processing until after recommendation")
        
        # Step 2B: Process remaining pages in parallel (if any)
        # All pages now use the established document_id from first page
        if len(page_tasks) > 1:
            remaining_pages = page_tasks[1:]
            logger.info(f"Processing remaining {len(remaining_pages)} pages in parallel (using document_id: {final_document_id})...")
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
                        "document_id": None,
                        "page_id": None
                    })
                else:
                    result_dict, result_state = result
                    page_results.append(result_dict)
                    if result_state:
                        page_states[result_dict["global_page_index"]] = result_state
        
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
        
        # Step 3B: Process/Update document with recommendation data (category and location)
        # IMPORTANT: 
        # - For single-page documents: Process first page here (deferred from Step 2A) with recommendation
        # - For multi-page documents: Update first page (already processed in Step 2A) with recommendation
        total_pages = len(page_results)
        if final_document_id and recommendation_result and recommendation_result.get("status") == "llm_success":
            rec_data = recommendation_result.get("recommendation", {})
            category_id = rec_data.get("category_id")
            location_id = rec_data.get("location_id") or rec_data.get("suggested_location_id")
            # Normalize: None means no location, convert to -1
            if location_id is None:
                location_id = -1
            
            # Update document by processing a page with category and location
            # We always update if we have recommendation data (even if category_id is None, we may need to set location_id to -1)
            # Use page_number=1 to trigger the update logic in DocumentService
            if total_pages == 1:
                logger.info(f"Step 3B: Processing single-page document {final_document_id} with recommendation data: category_id={category_id}, location_id={location_id}, rec_data keys: {list(rec_data.keys())}, rec_data: {rec_data}")
            else:
                logger.info(f"Step 3B: Updating multi-page document {final_document_id} (total_pages={total_pages}) with recommendation data: category_id={category_id}, location_id={location_id}, rec_data keys: {list(rec_data.keys())}, rec_data: {rec_data}")
            # Always try to update if we have recommendation result
            # category_id should always be present in rec_data if recommendation succeeded
            # Even if category_id is None, we should still try to update (it means no category was found)
            # IMPORTANT: Check if category_id exists AND is not None, or if location_id is not None
            has_category = "category_id" in rec_data and rec_data.get("category_id") is not None
            has_location = location_id is not None
            logger.info(f"Step 3B: Condition check - has_category: {has_category}, has_location: {has_location}, category_id value: {category_id}")
            if has_category or has_location:
                logger.info(f"Step 3B: Condition met - will update document")
                try:
                    # Get the first page state and result
                    first_page_state = None
                    first_page_result = None
                    for page_state in page_states.values():
                        if page_state and page_state.file_url:
                            # For multi-page: match by document_id
                            # For single-page: just get the first one
                            if total_pages > 1:
                                if page_state.document_id == final_document_id:
                                    first_page_state = page_state
                                    break
                            else:
                                first_page_state = page_state
                                break
                    
                    # Find corresponding first_page_result
                    if first_page_state:
                        for result in page_results:
                            if result.get("status") == "success":
                                # Match by global_page_index or by being the first successful result
                                if total_pages == 1 or result.get("page_number") == 1:
                                    first_page_result = result
                                    break
                    
                    if first_page_state and first_page_result:
                        # Create a temporary state with recommendation data for updating document
                        update_state = PipelineState(
                            image_url=first_page_state.image_url,
                            owner_id=owner_id,
                            document_id=final_document_id if total_pages > 1 else None,  # For single-page, let it create new document
                            file_url=first_page_state.file_url
                        )
                        update_state.recommendation_result = recommendation_result
                        
                        # Process with page_number=1
                        # For single-page: This creates the page record with recommendation
                        # For multi-page: This updates the existing page record with recommendation
                        global_page_num = first_page_result.get("global_page_number") or first_page_result.get("page_number", 1)
                        await pipeline.step_process_document(update_state, page_number=global_page_num)
                        
                        # Update final_document_id if we got one from processing
                        if update_state.document_id is not None:
                            final_document_id = update_state.document_id
                        
                        # Update first_page_result with values from step_process_document
                        if total_pages == 1:
                            # Single-page: Mark as processed
                            if update_state.page_id is not None:
                                first_page_result["page_id"] = update_state.page_id
                                logger.info(f"Single-page document processed successfully: page_id={update_state.page_id}, document_id={update_state.document_id}, category_id={category_id}, location_id={location_id}")
                            if update_state.document_id is not None:
                                first_page_result["document_id"] = update_state.document_id
                            if update_state.file_url is not None:
                                first_page_result["file_url"] = update_state.file_url
                            if update_state.processed_page_number is not None:
                                first_page_result["page_number"] = update_state.processed_page_number
                        else:
                            # Multi-page: Just update
                            logger.info(f"Updated multi-page document {final_document_id} with category_id={category_id}, location_id={location_id}")
                    else:
                        logger.warning(f"Could not find first page state to update document {final_document_id} with recommendation data")
                except Exception as e:
                    logger.error(f"Step 3B: Failed to update document with recommendation data: {e}", exc_info=True)
            else:
                logger.warning(f"Step 3B: Skipping update - category_id is None and location_id is None. rec_data: {rec_data}")
        elif total_pages == 1:
            logger.info(f"Step 3B: Skipping update - single page document, first page was already processed with recommendation in Step 2A")
        else:
            if not final_document_id:
                logger.warning("Step 3B: Skipping - no final_document_id")
            elif not recommendation_result:
                logger.warning("Step 3B: Skipping - no recommendation_result")
            elif recommendation_result.get("status") != "llm_success":
                logger.warning(f"Step 3B: Skipping - recommendation status is not llm_success: {recommendation_result.get('status')}")
        
        # Step 3C: Save embedding to DataStorageService via API
        # This should be done after pages are processed (document_id is available)
        # Only track embedding_save_error when a save operation is actually attempted and fails
        # Do not set error when save wasn't attempted due to missing preconditions
        embedding_save_error = None
        if final_document_id and embedding_result and isinstance(embedding_result, EmbeddingResult) and embedding_result.is_successful:
            # All preconditions met - attempt to save embedding
            try:
                # Convert document_id to int if needed
                doc_id = final_document_id
                if isinstance(doc_id, str):
                    try:
                        doc_id = int(doc_id)
                    except (ValueError, TypeError):
                        # Precondition check failed - cannot convert document_id
                        # Don't set embedding_save_error as no save was attempted
                        logger.warning(f"Could not convert document_id '{doc_id}' to int for embedding save - skipping save")
                        doc_id = None
                
                if doc_id and isinstance(doc_id, int):
                    embedding_vector = embedding_result.vector
                    if embedding_vector and len(embedding_vector) == 768:
                        # Actually attempt to save - this is where we track failures
                        save_success = await pipeline.pipeline_storage.save_document_embedding(
                            document_id=doc_id,
                            embedding=embedding_vector
                        )
                        if save_success:
                            logger.info(f"Document embedding saved successfully via API for document_id={doc_id}")
                        else:
                            # Save was attempted but failed - track this as an error
                            error_msg = f"Failed to save document embedding via API for document_id={doc_id}"
                            logger.warning(error_msg)
                            embedding_save_error = error_msg
                    else:
                        # Precondition check failed - dimension mismatch
                        # Don't set embedding_save_error as no save was attempted
                        logger.warning(f"Embedding vector dimension mismatch: expected 768, got {len(embedding_vector) if embedding_vector else 0} - skipping save")
                else:
                    # Precondition check failed - invalid document_id
                    # Don't set embedding_save_error as no save was attempted
                    logger.warning(f"Invalid document_id for embedding save: {final_document_id} - skipping save")
            except Exception as e:
                # Exception during save attempt - track as error
                # This exception occurred during the save attempt, so it's a real failure
                error_msg = f"Error saving document embedding via API: {str(e)}"
                logger.error(error_msg, exc_info=True)
                embedding_save_error = error_msg
                # Don't fail the pipeline if embedding save fails, but track the error
        else:
            # Save wasn't attempted due to missing preconditions - don't set embedding_save_error
            # These are expected conditions, not errors
            if not final_document_id:
                logger.info("Skipping embedding save: document_id not available (this is expected for failed page processing)")
            elif not embedding_result or not (isinstance(embedding_result, EmbeddingResult) and embedding_result.is_successful):
                logger.info("Skipping embedding save: embedding generation failed or not successful (this is expected when embedding generation fails)")
        
        # Step 4B: Process document page metadata for each successful page
        # This sends structured results and file storage path to /documents/process API
        # Note: First page was already processed in Step 2A, so skip it here
        logger.info("Processing document pages metadata after pipeline completion...")
        processed_page_keys = set()  # Track (document_id, page_number) combinations that have been processed
        
        for page_result in processed_results:
            if page_result.get("status") == "success":
                # 🔧 BUG FIX: Skip pages that already have page_id (already processed in Step 2A)
                # Check page_result first, as it's updated directly in Step 2A
                page_id = page_result.get("page_id")
                document_id = page_result.get("document_id")
                page_number = page_result.get("page_number")
                
                if page_id is not None:
                    logger.info(f"Skipping page (page_id={page_id}, page_number={page_number}, document_id={document_id}) - already processed in Step 2A")
                    # Track this page as processed
                    if document_id is not None and page_number is not None:
                        processed_page_keys.add((document_id, page_number))
                    continue
                
                global_page_index = page_result.get("global_page_index")
                page_state = page_states.get(global_page_index)
                if page_state:
                    # 🔧 BUG FIX: Use global_page_number for database (ensures uniqueness across all files)
                    # This is critical: when multiple files are uploaded, each file's first page would have page_number=1
                    # But in the database, page_number must be unique within a document
                    # So we use global_page_number (sequential across all files) instead of file-internal page_number
                    global_page_num = page_result.get("global_page_number") or page_result.get("page_number", 1)
                    file_page_number = page_result.get("page_number", 1)
                    
                    # Double-check: also check page_state.page_id as fallback
                    if page_state.page_id is not None:
                        logger.info(f"Skipping page (global_index={global_page_index}, page_id={page_state.page_id}, global_page_number={global_page_num}) - already processed in Step 2A")
                        # Track this page as processed (use global_page_num, not file page_number)
                        if page_state.document_id is not None:
                            processed_page_keys.add((page_state.document_id, global_page_num))
                        continue
                    
                    # Additional safety check: if we have document_id and global_page_number, check if already processed
                    if final_document_id:
                        page_key = (final_document_id, global_page_num)
                        if page_key in processed_page_keys:
                            logger.info(f"Skipping page (document_id={final_document_id}, global_page_number={global_page_num}) - already in processed set")
                            continue
                    
                    logger.info(f"Processing page: global_page_number={global_page_num}, file_page_number={file_page_number}, document_id={final_document_id}, global_index={global_page_index}")

                    # Update document_id in state if we have final_document_id
                    if final_document_id and not page_state.document_id:
                        page_state.document_id = final_document_id
                    
                    # Add recommendation_result to page_state if available (for Step 4B processing)
                    if recommendation_result and not page_state.recommendation_result:
                        page_state.recommendation_result = recommendation_result
                    
                    # Process document page with structured results (use global page number for database)
                    await pipeline.step_process_document(page_state, page_number=global_page_num)

                    # Update page_result with values returned from /documents/process
                    if page_state.page_id is not None:
                        page_result["page_id"] = page_state.page_id
                        logger.info(f"Page processed successfully: page_id={page_state.page_id}, document_id={page_state.document_id}, page_number={global_page_num}")
                    else:
                        # If page_id is None, document processing may have failed
                        # Update error status in page_result
                        if page_state.file_upload_error:
                            page_result["error"] = page_state.file_upload_error
                            logger.warning(f"Page processing failed: {page_state.file_upload_error} (page_number={global_page_num})")
                        else:
                            logger.warning(f"Page processing returned no page_id (page_number={global_page_num}) - this may indicate a failure")
                    
                    if page_state.document_id is not None:
                        page_result["document_id"] = page_state.document_id
                        # Track this page as processed (use global_page_num for tracking)
                        if page_state.page_id is not None:
                            processed_page_keys.add((page_state.document_id, global_page_num))
                    if page_state.file_url is not None:
                        page_result["file_url"] = page_state.file_url
                    # Prefer page number returned by storage service as source of truth
                    if page_state.processed_page_number is not None:
                        page_result["page_number"] = page_state.processed_page_number

                    # 如果还没有最终 document_id，则从任意一个成功页中补充
                    if final_document_id is None and page_state.document_id is not None:
                        final_document_id = page_state.document_id

        # 清理内部字段，不暴露给 API 调用方
        for page_result in processed_results:
            page_result.pop("global_page_index", None)
        
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
        
        # Determine status: consider both page processing and embedding save
        base_status = "success" if failed_pages == 0 else ("partial_success" if successful_pages > 0 else "failed")
        # If embedding save failed, downgrade status (success -> partial_success, partial_success stays partial_success)
        if embedding_save_error and base_status == "success":
            base_status = "partial_success"
        
        # Check recommendation result status - if it failed, mark as failed
        recommendation_error = None
        if recommendation_result:
            recommendation_status = recommendation_result.get("status")
            if recommendation_status == "llm_error":
                recommendation_error = recommendation_result.get("error", "Recommendation generation failed after multiple retries.")
                # If recommendation failed, mark the entire ingestion as failed
                # Recommendation is a critical step for document classification and storage location
                base_status = "failed"
                logger.error(f"Recommendation failed: {recommendation_error}. Marking ingestion as failed.")
        
        response = {
            "status": base_status,
            "document_id": document_id_int,
            "total_pages": total_pages,
            "successful_pages": successful_pages,
            "failed_pages": failed_pages,
            "page_results": processed_results
        }
        
        # Add embedding save error if present
        if embedding_save_error:
            response["embedding_save_error"] = embedding_save_error
        
        # Add recommendation error if present
        if recommendation_error:
            response["recommendation_error"] = recommendation_error
        
        # Add recommendation data if available
        if recommendation_result and recommendation_result.get("status") == "llm_success":
            rec_data = recommendation_result.get("recommendation", {}).copy()
            
            # Normalize location fields: use location_id instead of location_name
            # Prefer location_id over suggested_location_id
            # If no location is provided, set location_id to -1
            location_id = rec_data.get("location_id") or rec_data.get("suggested_location_id")
            rec_data["location_id"] = location_id if location_id else -1
            
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
        if embedding_result and isinstance(embedding_result, EmbeddingResult) and embedding_result.is_successful:
            response["embedding"] = embedding_result.vector
            response["embedding_dimension"] = embedding_result.dimension
        
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
