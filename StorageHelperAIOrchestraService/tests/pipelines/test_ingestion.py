import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.pipelines.ingestion import IngestionPipeline, PipelineState, OCRResult, EmbeddingResult, VisionResult

@pytest.fixture
def mock_ocr():
    return AsyncMock(return_value=OCRResult(text="OCR Text", confidence=90.0))

@pytest.fixture
def mock_cleaner():
    return AsyncMock(return_value={"cleaned_text": "Cleaned Text", "original_length": 8, "cleaned_length": 12})

@pytest.fixture
def mock_recommendation():
    return AsyncMock(return_value={"status": "llm_success", "recommendation": {"category_id": 1, "location_id": 1}})

@pytest.fixture
def mock_embedding():
    mock = MagicMock()
    mock.generate = AsyncMock(return_value=EmbeddingResult(vector=[0.1]*768, dimension=768, status="success"))
    mock.model_name = "test-model"
    mock.task_type = "test-task"
    return mock

@pytest.fixture
def mock_vision():
    mock = MagicMock()
    mock.analyze_image = AsyncMock(return_value=VisionResult(description="Vision Desc", confidence=0.9, detected_elements=[], has_text=False))
    return mock

@pytest.mark.asyncio
async def test_pipeline_run_success(mock_ocr, mock_cleaner, mock_recommendation, mock_embedding, mock_vision):
    with patch('app.storage.pipeline_storage.PipelineStorage', return_value=MagicMock()):
        pipeline = IngestionPipeline(
            ocr_extractor=mock_ocr,
            text_cleaner=mock_cleaner,
            recommendation_generator=mock_recommendation,
            embedding_generator=mock_embedding,
            vision_analyzer=mock_vision
        )
        
        # Mock storage methods
        pipeline.pipeline_storage.upload_file_only = AsyncMock(return_value="http://storage/file.jpg")
        pipeline.pipeline_storage.process_document_page = AsyncMock(return_value={"document_id": 123, "page_id": 456})
        pipeline.pipeline_storage.save_document_embedding = AsyncMock(return_value=True)
        
        result = await pipeline.run(image_url="http://input/image.jpg", owner_id=1)
        
        assert result["status"] == "completed"
        assert result["document_id"] == 123
        assert "OCR" in result["processing_steps"]
        assert "Recommendation" in result["processing_steps"]
        assert "Embedding" in result["processing_steps"]

@pytest.mark.asyncio
async def test_pipeline_ocr_failure(mock_ocr, mock_cleaner, mock_recommendation, mock_embedding, mock_vision):
    mock_ocr.return_value = None # Simulate failure
    
    with patch('app.storage.pipeline_storage.PipelineStorage', return_value=MagicMock()):
        pipeline = IngestionPipeline(
            ocr_extractor=mock_ocr,
            text_cleaner=mock_cleaner,
            recommendation_generator=mock_recommendation,
            embedding_generator=mock_embedding,
            vision_analyzer=mock_vision
        )
        
        result = await pipeline.run(image_url="http://input/image.jpg", owner_id=1)
        
        assert result["status"] == "failed"
        assert "OCR" not in result["processing_steps"]

@pytest.mark.asyncio
async def test_pipeline_vision_merge(mock_ocr, mock_cleaner, mock_recommendation, mock_embedding, mock_vision):
    with patch('app.storage.pipeline_storage.PipelineStorage', return_value=MagicMock()), \
         patch('app.core.config.settings.VISION_ENABLE', True), \
         patch('app.core.config.settings.VISION_AUTO_TRIGGER_ON_LOW_OCR', False):
        
        pipeline = IngestionPipeline(
            ocr_extractor=mock_ocr,
            text_cleaner=mock_cleaner,
            recommendation_generator=mock_recommendation,
            embedding_generator=mock_embedding,
            vision_analyzer=mock_vision
        )
        
        pipeline.pipeline_storage.upload_file_only = AsyncMock(return_value="http://storage/file.jpg")
        pipeline.pipeline_storage.process_document_page = AsyncMock(return_value={"document_id": 123})
        
        state = PipelineState(image_url="test.jpg", owner_id=1)
        await pipeline.step_ocr(state)
        await pipeline.step_vision_enhancement(state)
        
        assert "Vision Enhancement" in state.processing_steps
        assert "OCR Extracted Text" in state.ocr_result.text
        assert "Vision Desc" in state.ocr_result.text

