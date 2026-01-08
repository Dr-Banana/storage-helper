import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.modules.vision import VisionAnalyzer, VisionResult

@pytest.mark.asyncio
async def test_vision_analyzer_disabled():
    analyzer = VisionAnalyzer(enable_vision=False)
    result = await analyzer.analyze_image("test.jpg")
    assert result.description == ""
    assert result.confidence == 0.0

@pytest.mark.asyncio
async def test_vision_analyzer_no_api_key():
    with patch('os.getenv', return_value=""):
        analyzer = VisionAnalyzer(api_key="")
        result = await analyzer.analyze_image("test.jpg")
        assert result.description == ""

@pytest.mark.asyncio
async def test_vision_analyzer_analyze_success():
    analyzer = VisionAnalyzer(api_key="fake_key", model_name="gemini-pro-vision")
    
    mock_resp = {
        "candidates": [{
            "content": {
                "parts": [{"text": "This is a photo of a receipt with a logo and some printed text."}]
            }
        }]
    }
    
    with patch.object(VisionAnalyzer, '_load_image', new_callable=AsyncMock) as mock_load, \
         patch.object(VisionAnalyzer, '_call_gemini_vision', new_callable=AsyncMock) as mock_call:
        
        mock_load.return_value = b"fake_image_data"
        mock_call.return_value = mock_resp
        
        result = await analyzer.analyze_image("test.jpg")
        
        assert "receipt" in result.description
        assert 'photo' in result.detected_elements
        assert 'logo' in result.detected_elements
        assert result.has_text is True
        assert result.confidence > 0.8

def test_parse_response_empty():
    analyzer = VisionAnalyzer(api_key="fake_key", model_name="test")
    result = analyzer._parse_response({})
    assert result.description == ""
    assert result.confidence == 0.0

@pytest.mark.asyncio
async def test_call_gemini_vision_retry():
    analyzer = VisionAnalyzer(api_key="fake_key", model_name="test")
    
    # Mock response for 429
    mock_response_429 = MagicMock()
    mock_response_429.status_code = 429
    # The code calls raise_for_status() if status_code == 429
    import httpx
    mock_response_429.raise_for_status.side_effect = httpx.HTTPStatusError("Rate Limit", request=MagicMock(), response=mock_response_429)
    
    # Mock response for 200
    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {"candidates": [{"content": {"parts": [{"text": "success"}]}}]}
    mock_response_200.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post, \
         patch('asyncio.sleep', new_callable=AsyncMock):
        
        # First call 429, second call 200
        mock_post.side_effect = [mock_response_429, mock_response_200]
        
        result = await analyzer._call_gemini_vision("base64", "prompt")
        assert result["candidates"][0]["content"]["parts"][0]["text"] == "success"
        assert mock_post.call_count == 2
