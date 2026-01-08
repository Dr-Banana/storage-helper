import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from app.modules.recommendation import RecommendationGenerator

@pytest.fixture
def generator():
    return RecommendationGenerator(api_key="fake_key")

def test_score_location_for_category(generator):
    # Mock category keywords
    with patch('app.modules.recommendation.get_category_keywords', return_value=['kitchen', 'food']):
        location = {'name': 'Kitchen Shelf', 'description': 'Place for food'}
        score = generator.score_location_for_category('REC', location)
        assert score == 2
        
        location2 = {'name': 'Garage', 'description': 'Car tools'}
        score2 = generator.score_location_for_category('REC', location2)
        assert score2 == 0

def test_find_best_location_any(generator):
    locations = [
        {'id': 1, 'name': 'Kitchen', 'description': 'food'},
        {'id': 2, 'name': 'Office', 'description': 'papers'}
    ]
    
    with patch('app.modules.recommendation.get_category_keywords', side_effect=lambda code: ['food'] if code == 'REC' else []):
        best_id = generator.find_best_location_any('REC', locations)
        assert best_id == 1
        
        best_id_none = generator.find_best_location_any('TAX', locations)
        assert best_id_none == 1 # Defaults to first if no match

@pytest.mark.asyncio
async def test_generate_recommendation_success(generator):
    document_text = "Receipt for groceries from yesterday"
    owner_id = 1
    
    # Mock Gemini response
    mock_gemini_resp = {
        'candidates': [{
            'content': {
                'parts': [{
                    'text': json.dumps({
                        "category_code": "REC",
                        "suggested_location_id": 1,
                        "suggested_location_name": "Kitchen",
                        "suggested_tags": ["grocery", "receipt"],
                        "recommendation_reason": "It is a receipt for food."
                    })
                }]
            }
        }]
    }
    
    mock_category = {'id': 10, 'code': 'REC', 'name': 'Receipt'}
    mock_locations = [{'id': 1, 'name': 'Kitchen', 'description': 'food'}]
    
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post, \
         patch('app.modules.recommendation.is_allowed_category_type', return_value=True), \
         patch('app.modules.recommendation.get_category_suggestion', return_value={'name': 'Receipt'}), \
         patch.object(RecommendationGenerator, 'ensure_category_exists', return_value=mock_category), \
         patch.object(RecommendationGenerator, 'load_locations', return_value=mock_locations), \
         patch.object(RecommendationGenerator, 'get_preferred_location_for_category', return_value=None), \
         patch.object(RecommendationGenerator, 'find_best_unused_location', return_value=1):
        
        mock_post.return_value = MagicMock(status_code=200, json=lambda: mock_gemini_resp, raise_for_status=MagicMock())
        
        result = await generator.generate(document_text, owner_id)
        
        assert result["status"] == "llm_success"
        assert result["recommendation"]["category_code"] == "REC"
        assert result["recommendation"]["category_id"] == 10
        assert result["recommendation"]["suggested_location_id"] == 1

@pytest.mark.asyncio
async def test_generate_recommendation_api_error(generator):
    with patch('httpx.AsyncClient.post', side_effect=Exception("Gemini Offline")):
        result = await generator.generate("some text", 1)
        assert result["status"] == "llm_error"
        # The generator catches the exception and returns a failed status after retries
        assert "Failed to generate recommendation" in result["error"]

def test_add_new_category_code_generation():
    generator = RecommendationGenerator(api_key="fake")
    
    # 模拟 API 返回已存在的分类
    existing_categories = [
        {"code": "HOM", "name": "Home"},
        {"code": "TAX", "name": "Tax"}
    ]
    
    with patch.object(generator, 'save_category_to_api') as mock_save, \
         patch.object(generator, 'load_document_categories', return_value=existing_categories):
        
        # 1. 测试从允许列表中自动选择一个未使用的码
        # 假设 ALLOWED_CATEGORY_TYPES = ['TAX', 'MED', 'REC', ...]
        
        # 2. 测试根据名称生成 (当允许列表都用完或不匹配时)
        # 模拟 is_allowed_category_type 总是返回 False 强制触发回退
        with patch('app.modules.recommendation.is_allowed_category_type', return_value=False), \
             patch('app.modules.recommendation.ALLOWED_CATEGORY_TYPES', []):
            
            mock_save.return_value = {"code": "TES", "name": "Test Category"}
            
            # 第一次生成 TES
            generator.add_new_category(user_id=1, name="Test Category", description="desc", existing_categories=[])
            mock_save.assert_called_with(1, "TES", "Test Category", "desc", None)
            
            # 如果 TES 已存在，生成 TES1
            mock_save.reset_mock()
            existing = [{"code": "TES", "name": "Test"}]
            generator.add_new_category(user_id=1, name="Test Category", description="desc", existing_categories=existing)
            mock_save.assert_called_with(1, "TES1", "Test Category", "desc", None)

def test_ensure_category_exists_logic():
    generator = RecommendationGenerator(api_key="fake")
    existing = [{"code": "TAX", "id": 100, "name": "Tax"}]
    
    # 如果已存在，不应该调用 add_new_category
    with patch.object(generator, 'load_document_categories', return_value=existing), \
         patch.object(generator, 'add_new_category') as mock_add:
        
        res = generator.ensure_category_exists(user_id=1, code="TAX", name="Tax", description="d")
        assert res["id"] == 100
        mock_add.assert_not_called()
