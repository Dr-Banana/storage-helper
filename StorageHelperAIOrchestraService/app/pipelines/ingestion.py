from typing import Dict, Any, Optional, Union, Callable
import logging
from dataclasses import dataclass, field

# Import module types
from app.modules.ocr import OCRResult, extract_text_advanced
from app.modules.cleaning import process_text
from app.modules.recommendation import generate_recommendation
from app.modules.embedding import EmbeddingGenerator
from app.integrations.storage_client import persist_document
from app.storage.local_storage import LocalStorage, save_document, save_error_document
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    """State container for pipeline execution data."""
    image_url: str
    owner_id: int
    document_id: Optional[Union[int, str]] = None  # Can be int (from DB) or str (UUID from local storage)
    
    # Processing results
    ocr_result: Optional[OCRResult] = None
    cleaned_text: Optional[str] = None
    cleaning_info: Optional[Dict[str, Any]] = None
    recommendation_result: Optional[Dict[str, Any]] = None
    embedding: Optional[list] = None
    embedding_status: str = "pending"
    
    # Pipeline metadata
    processing_steps: list = field(default_factory=list)
    status: str = "initialized"
    error: Optional[str] = None
    
    def to_output_dict(self) -> Dict[str, Any]:
        """Convert pipeline state to output dictionary."""
        output = {
            "status": self.status,
            "owner_id": self.owner_id,
            "source": self.image_url,
            "document_id": str(self.document_id) if self.document_id is not None else None,
            "processing_steps": self.processing_steps.copy(),
        }
        
        if self.ocr_result:
            output.update({
                "extracted_text": self.cleaned_text or self.ocr_result.text,  # Use cleaned text if available
                "ocr_confidence": self.ocr_result.confidence,
                "raw_ocr_info": self.ocr_result.to_dict(),
            })
        
        if self.cleaning_info:
            output["cleaning_info"] = self.cleaning_info
        
        if self.recommendation_result:
            output["recommendation_status"] = self.recommendation_result.get("status")
            if self.recommendation_result.get("status") == "llm_success":
                output["recommendation_data"] = self.recommendation_result.get("recommendation")
            else:
                output["recommendation_error"] = self.recommendation_result.get("error")
        
        if self.embedding:
            output.update({
                "embedding": self.embedding,
                "embedding_dimension": len(self.embedding),
                "embedding_status": self.embedding_status,
            })
        
        if self.error:
            output["error"] = self.error
        
        return output


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
        storage_client: Optional[Callable] = None,
    ):
        """
        Initialize the ingestion pipeline with module dependencies.
        
        :param ocr_extractor: Function/class for OCR text extraction. Defaults to extract_text_advanced.
        :param text_cleaner: Function for cleaning OCR text. Defaults to process_text from cleaning module.
        :param recommendation_generator: Function for generating recommendations. Defaults to generate_recommendation.
        :param embedding_generator: EmbeddingGenerator instance. Defaults to new EmbeddingGenerator().
        :param storage_client: Function for persisting documents. Defaults to persist_document.
        """
        # Set defaults for module dependencies
        self.ocr_extractor = ocr_extractor or extract_text_advanced
        self.text_cleaner = text_cleaner or process_text
        self.recommendation_generator = recommendation_generator or generate_recommendation
        self.embedding_generator = embedding_generator or EmbeddingGenerator()
        self.storage_client = storage_client or persist_document
        
        logger.info("IngestionPipeline initialized with module dependencies")
    
    async def step_ocr(self, state: PipelineState) -> bool:
        """
        Step 1: Extract text from image using OCR.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        logger.info(f"STEP 1 (OCR): Processing image from {state.image_url}")
        
        try:
            state.ocr_result = await self.ocr_extractor(state.image_url)
            
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
        Step 4: Persist document data to storage service and local storage.
        
        :param state: Pipeline state to update.
        :param is_error: If True, save to error directory instead of documents
        :return: True if successful, False otherwise.
        """
        if is_error:
            logger.warning("STEP 4 (Error Persistence): Saving failed document to error directory...")
        else:
            logger.info("STEP 4 (Persistence): Persisting document to storage service and local storage...")
        
        try:
            # Prepare document data for persistence
            document_data = state.to_output_dict()
            
            # Add image_path to document_data so it can be saved to images/ directory
            document_data["image_path"] = state.image_url
            
            if is_error:
                # Save to error directory with full context
                error_info = {
                    "status": state.status,
                    "error": state.error,
                    "failed_step": self._get_failed_step(state),
                    "processing_steps": state.processing_steps
                }
                error_doc_id = save_error_document(document_data, error_info)
                state.document_id = error_doc_id
                logger.warning(f"⚠️  Failed document saved to error directory: {error_doc_id}")
                logger.warning(f"    This document can be reviewed and retried later.")
                return True
            else:
                # Save to normal storage (tmp folder) - this generates and returns a UUID string
                # The save_document function will also save the image to images/ directory
                local_doc_id = save_document(document_data)
                logger.info(f"✓ Document saved to local storage: {local_doc_id}")
                
                # Always use the local UUID as the document_id (local storage generates UUID strings)
                # If remote storage returns an ID, we could use it, but for consistency, use local UUID
                state.document_id = local_doc_id
                
                # Also persist using storage client (if available)
                persisted_id = None
                if self.storage_client:
                    # Update document_data with the new document_id before sending to storage client
                    document_data["document_id"] = local_doc_id
                    persisted_id = await self.storage_client(document_data)
                    # Note: We keep using local_doc_id (UUID string) even if persisted_id is returned
                    # This ensures consistency with the saved document file
                state.processing_steps.append("Persistence")
                state.status = "completed"
                logger.info(f"STEP 4 (Persistence) Complete. Local Document ID: {local_doc_id}, Remote ID: {persisted_id}")
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
        skip_persist: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the complete ingestion pipeline.
        
        :param image_url: URL or path of the image to process.
        :param owner_id: ID of the user who owns the document.
        :param document_id: Optional existing document ID.
        :param skip_persist: If True, skip the persistence step.
        :return: Dictionary containing the processed document data.
        """
        # Initialize pipeline state
        state = PipelineState(
            image_url=image_url,
            owner_id=owner_id,
            document_id=document_id
        )
        
        logger.info(f"Pipeline started for document_id={document_id}, processing image from: {image_url}")
        
        # Step 1: OCR
        if not await self.step_ocr(state):
            return state.to_output_dict()
        
        # Step 2: Cleaning (after OCR, before recommendation/embedding)
        await self.step_cleaning(state)
        
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
    document_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Backward compatibility wrapper for run_ingestion_pipeline function.
    Uses the default IngestionPipeline instance.
    
    :param image_url: URL or path of the image to process.
    :param owner_id: ID of the user who owns the document.
    :param document_id: Optional existing document ID.
    :return: Dictionary containing the processed document data.
    """
    return await _default_pipeline.run(image_url, owner_id, document_id)
