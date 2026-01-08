import pytest
import os
from app.core.config import mask_sensitive_value, get_env_file

def test_mask_sensitive_value():
    assert mask_sensitive_value("12345678") == "****5678"
    assert mask_sensitive_value("abc") == "***"
    assert mask_sensitive_value("") == "[NOT SET]"
    assert mask_sensitive_value(None) == "[NOT SET]"

def test_get_env_file_logic():
    # 测试在不同 APP_ENV 下的行为
    with patch('os.getenv') as mock_env:
        # 1. 模拟 prod
        mock_env.return_value = "prod"
        # 即使文件不存在，prod 也允许返回 None 而不退出 (依赖系统变量)
        with patch('os.path.exists', return_value=False):
            assert get_env_file() is None
            
        # 2. 模拟无效环境
        mock_env.return_value = "invalid"
        with pytest.raises(SystemExit):
            get_env_file()

from unittest.mock import patch

