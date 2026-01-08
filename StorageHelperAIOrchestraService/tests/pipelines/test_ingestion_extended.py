import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.pipelines.ingestion import run_unified_ingestion_pipeline

@pytest.mark.asyncio
async def test_run_unified_ingestion_pipeline_multi_file():
    # 模拟多文件处理逻辑
    file_urls = ["test1.jpg", "test2.jpg"]
    owner_id = 1
    
    # 模拟内部调用的 convert_pdf_to_images (如果是 PDF)
    # 这里我们只测图片模式，主要看是否正确分发了任务
    with patch('app.modules.ocr.detect_file_type', return_value="image"), \
         patch('app.storage.pipeline_storage.PipelineStorage.upload_file_only', new_callable=AsyncMock) as mock_upload, \
         patch('app.pipelines.ingestion.IngestionPipeline.step_ocr', new_callable=AsyncMock) as mock_ocr, \
         patch('app.pipelines.ingestion.IngestionPipeline.step_cleaning', new_callable=AsyncMock) as mock_clean, \
         patch('app.modules.recommendation.generate_recommendation', new_callable=AsyncMock) as mock_rec, \
         patch('app.modules.embedding.EmbeddingGenerator.generate', new_callable=AsyncMock) as mock_embed:
        
        mock_upload.return_value = "http://storage/file.jpg"
        mock_ocr.return_value = True
        mock_clean.return_value = True
        mock_rec.return_value = {"status": "llm_success", "recommendation": {"category_id": 1}}
        mock_embed.return_value = MagicMock(is_successful=True, vector=[0.1]*768, dimension=768)
        
        result = await run_unified_ingestion_pipeline(file_urls, owner_id, preview_mode=True)
        
        assert result["status"] == "success"
        assert result["total_pages"] == 2
        assert len(result["page_results"]) == 2
        # 验证是否上传了两次 (每个文件一次)
        assert mock_upload.call_count == 2

@pytest.mark.asyncio
async def test_run_unified_ingestion_pipeline_partial_failure():
    # 模拟其中一个页面失败的情况
    file_urls = ["good.jpg", "bad.jpg"]
    
    with patch('app.modules.ocr.detect_file_type', return_value="image"), \
         patch('app.storage.pipeline_storage.PipelineStorage.upload_file_only', new_callable=AsyncMock, return_value="url"), \
         patch('app.pipelines.ingestion.IngestionPipeline.step_ocr', new_callable=AsyncMock) as mock_ocr, \
         patch('app.pipelines.ingestion.IngestionPipeline.step_cleaning', new_callable=AsyncMock, return_value=True), \
         patch('app.modules.recommendation.generate_recommendation', new_callable=AsyncMock), \
         patch('app.modules.embedding.EmbeddingGenerator.generate', new_callable=AsyncMock):
        
        # 第一次成功，第二次失败
        mock_ocr.side_effect = [True, False]
        
        result = await run_unified_ingestion_pipeline(file_urls, owner_id=1, preview_mode=True)
        
        # 应该返回 partial_success (或者根据你的业务逻辑是 success 包含错误项)
        # 根据代码 logic，如果成功的页面 > 0 且有失败，base_status 是 partial_success
        assert result["status"] == "partial_success"
        assert result["successful_pages"] == 1
        assert result["failed_pages"] == 1

