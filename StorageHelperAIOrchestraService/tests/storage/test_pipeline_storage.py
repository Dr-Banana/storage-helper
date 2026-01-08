import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.storage.pipeline_storage import LocationDataHandler, PipelineStorage

def test_location_data_handler_db_to_llm():
    db_locations = {
        1: ["Kitchen", "Shelf for food storage"],
        2: ["Office", "Drawer for tax papers and receipts"]
    }
    llm_format = LocationDataHandler.format_db_locations_for_llm(db_locations)
    
    assert llm_format[1]["name"] == "Kitchen"
    assert llm_format[1]["description"] == "Shelf for food storage"
    assert llm_format[2]["name"] == "Office"
    assert "tax papers" in llm_format[2]["description"]

def test_location_data_handler_llm_to_db():
    llm_locations = {
        1: {"name": "Kitchen", "description": "Shelf"}
    }
    db_format = LocationDataHandler.format_llm_locations_for_db(llm_locations)
    
    assert db_format[1] == ["Kitchen", "Shelf"]

@pytest.mark.asyncio
async def test_pipeline_storage_upload_file_only():
    mock_client = MagicMock()
    storage = PipelineStorage(storage_client=mock_client)
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"image_url": "http://storage/file.jpg"}
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post, \
         patch.object(PipelineStorage, '_read_file_content', new_callable=AsyncMock) as mock_read:
        
        mock_post.return_value = mock_response
        mock_read.return_value = b"fake content"
        
        result = await storage.upload_file_only("test.jpg", 1)
        assert result == "http://storage/file.jpg"

@pytest.mark.asyncio
async def test_pipeline_storage_process_document_page():
    mock_client = MagicMock()
    storage = PipelineStorage(storage_client=mock_client)
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "document_id": 123,
        "page_id": 456,
        "status": "success"
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await storage.process_document_page(
            image_url="http://storage/file.jpg",
            owner_id=1,
            page_number=1,
            ocr_text="some text"
        )
        
        assert result["document_id"] == 123
        assert result["page_id"] == 456
        assert result["status"] == "success"

