import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock
from main import app

client = TestClient(app)

def test_get_category_config():
    response = client.get("/api/v1/category-config")
    assert response.status_code == 200
    data = response.json()
    assert "allowed_category_types" in data
    assert len(data["category_types"]) > 0

@pytest.mark.asyncio
async def test_ingestion_endpoint():
    # Mock unified pipeline
    mock_result = {
        "status": "success",
        "document_id": 1,
        "recommendation": {
            "category_code": "REC",
            "category_id": 1,
            "recommendation_reason": "Test reason"
        },
        "total_pages": 1,
        "successful_pages": 1,
        "failed_pages": 0,
        "page_results": []
    }
    
    # Patch the function where it's used - but since it's a local import, 
    # we patch the module where it's defined and then mock the return value.
    with patch('app.pipelines.ingestion.run_unified_ingestion_pipeline', new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_result
        
        # Create a dummy file
        files = [('files', ('test.txt', b'content'))]
        data = {'owner_id': 1}
        
        response = client.post("/api/v1/ingestion", files=files, data=data)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["preview_mode"] is True

@pytest.mark.asyncio
async def test_confirm_endpoint():
    with patch('app.storage.pipeline_storage.PipelineStorage.process_document_page', new_callable=AsyncMock) as mock_process:
        mock_process.return_value = {"document_id": 1, "page_id": 1}
        
        confirm_data = {
            "owner_id": 1,
            "document_id": 1,
            "category_id": 1,
            "location_id": 1,
            "recommendation": {
                "category_code": "REC",
                "recommendation_reason": "Test reason"
            },
            "page_results": [
                {
                    "page_number": 1,
                    "status": "success",
                    "ocr_text": "text",
                    "file_url": "http://file.jpg"
                }
            ]
        }
        
        response = client.post("/api/v1/ingestion/confirm", json=confirm_data)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"

@pytest.mark.asyncio
async def test_search_endpoint():
    mock_vector = [0.1] * 768
    with patch('app.modules.embedding.EmbeddingGenerator.generate', new_callable=AsyncMock) as mock_embed, \
         patch('app.storage.pipeline_storage.PipelineStorage.search_documents', new_callable=AsyncMock) as mock_search:
        
        mock_embed.return_value = MagicMock(is_successful=True, vector=mock_vector)
        mock_search.return_value = [1, 2, 3]
        
        response = client.post("/api/v1/search", json={"query": "test query", "owner_id": 1})
        
        assert response.status_code == 200
        assert response.json()["document_ids"] == [1, 2, 3]
        assert response.json()["count"] == 3
