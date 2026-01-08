import pytest
from app.core.category_config import (
    is_allowed_category_type, 
    get_category_suggestion, 
    get_category_keywords,
    ALLOWED_CATEGORY_TYPES
)

def test_is_allowed_category_type():
    if len(ALLOWED_CATEGORY_TYPES) > 0:
        valid_type = ALLOWED_CATEGORY_TYPES[0]
        assert is_allowed_category_type(valid_type) is True
        assert is_allowed_category_type(valid_type.lower()) is True
    
    assert is_allowed_category_type("NON_EXISTENT_TYPE_123") is False

def test_get_category_suggestion():
    # 测试获取预定义建议
    res = get_category_suggestion("TAX")
    if res:
        assert "name" in res
        assert "description" in res

def test_get_category_keywords():
    # 测试关键字列表
    keywords = get_category_keywords("TAX")
    assert isinstance(keywords, list)

