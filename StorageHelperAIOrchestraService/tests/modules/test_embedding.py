import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.modules.embedding import EmbeddingGenerator, EmbeddingResult

def test_embedding_result_logic():
    # Success case
    res = EmbeddingResult(vector=[0.1, 0.2], dimension=2, status="success")
    assert res.is_successful is True
    assert res.to_dict()["dimension"] == 2
    
    # Failed case
    res_fail = EmbeddingResult.create_failed("Error message")
    assert res_fail.is_successful is False
    assert res_fail.status == "failed"
    assert res_fail.error == "Error message"

@pytest.mark.asyncio
async def test_embedding_generator_generate_success():
    generator = EmbeddingGenerator(api_key="fake_key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "embedding": {
            "values": [0.1, 0.2, 0.3]
        }
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await generator.generate("test text")
        
        assert result.is_successful is True
        assert result.vector == [0.1, 0.2, 0.3]
        assert result.dimension == 3

@pytest.mark.asyncio
async def test_embedding_generator_empty_text():
    generator = EmbeddingGenerator(api_key="fake_key")
    result = await generator.generate("")
    assert result.is_successful is False
    assert "Empty" in result.error

@pytest.mark.asyncio
async def test_embedding_generator_api_failure():
    generator = EmbeddingGenerator(api_key="fake_key", max_retries=1)
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("API Down")
        
        result = await generator.generate("test text")
        assert result.is_successful is False
        assert "API Down" in result.error

@pytest.mark.asyncio
async def test_generate_batch():
    generator = EmbeddingGenerator(api_key="fake_key")
    
    with patch.object(EmbeddingGenerator, 'generate', new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = EmbeddingResult(vector=[0.1], dimension=1)
        
        results = await generator.generate_batch(["text1", "text2"])
        assert len(results) == 2
        assert results[0].vector == [0.1]
        assert mock_gen.call_count == 2

