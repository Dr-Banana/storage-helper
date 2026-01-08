import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from PIL import Image
from io import BytesIO
from pathlib import Path
from app.modules.ocr import (
    get_tesseract_executable_name, 
    detect_file_type, 
    preprocess_image, 
    load_image_from_source,
    extract_text,
    OCRResult
)

def test_get_tesseract_executable_name():
    with patch('platform.system', return_value='Windows'):
        assert get_tesseract_executable_name() == 'tesseract.exe'
    with patch('platform.system', return_value='Linux'):
        assert get_tesseract_executable_name() == 'tesseract'

def test_detect_file_type():
    assert detect_file_type("test.pdf") == "pdf"
    assert detect_file_type("test.jpg") == "image"
    assert detect_file_type(b"%PDF-1.5") == "pdf"
    assert detect_file_type(b"\xff\xd8\xff") == "image"
    assert detect_file_type("unknown.txt") == "image"

def test_preprocess_image():
    # Create a small RGB image
    img = Image.new('RGB', (100, 100), color='white')
    processed_img, info = preprocess_image(img, enable_preprocessing=True)
    
    assert isinstance(processed_img, Image.Image)
    assert info["grayscale"] is True
    assert info["contrast_enhanced"] is True

def test_ocr_result_to_dict():
    result = OCRResult(text="Hello", confidence=95.0, source_type="image")
    d = result.to_dict()
    assert d["text"] == "Hello"
    assert d["confidence"] == 95.0
    assert d["source_type"] == "image"

@pytest.mark.asyncio
async def test_load_image_from_source_bytes():
    img = Image.new('RGB', (10, 10), color='red')
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()
    
    loaded_img = await load_image_from_source(img_bytes)
    assert loaded_img.size == (10, 10)

@pytest.mark.asyncio
async def test_load_image_from_source_local_file(tmp_path):
    img = Image.new('RGB', (10, 10), color='blue')
    img_path = tmp_path / "test.png"
    img.save(img_path)
    
    loaded_img = await load_image_from_source(img_path)
    assert loaded_img.size == (10, 10)

@pytest.mark.asyncio
async def test_load_image_from_source_url():
    img = Image.new('RGB', (10, 10), color='green')
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()
    
    mock_response = MagicMock()
    mock_response.content = img_bytes
    mock_response.raise_for_status = MagicMock()
    
    with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        loaded_img = await load_image_from_source("http://example.com/test.png")
        assert loaded_img.size == (10, 10)

@pytest.mark.asyncio
async def test_extract_text_advanced_image():
    # Mocking dependencies
    mock_img = Image.new('RGB', (10, 10))
    
    with patch('app.modules.ocr.load_image_from_source', new_callable=AsyncMock) as mock_load, \
         patch('pytesseract.image_to_string', return_value="Extracted Text"), \
         patch('pytesseract.image_to_data', return_value={'conf': [90, 95], 'text': ['Extracted', 'Text']}):
        
        mock_load.return_value = mock_img
        
        from app.modules.ocr import extract_text_advanced
        result = await extract_text_advanced("test.png")
        
        assert result.text == "Extracted Text"
        assert result.confidence == 92.5
        assert result.source_type == "image"

