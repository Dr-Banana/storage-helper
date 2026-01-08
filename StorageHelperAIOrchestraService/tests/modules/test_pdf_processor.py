import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path
from io import BytesIO
from PIL import Image
from app.modules.pdf_processor import (
    load_pdf_from_source,
    check_pdf_has_text,
    extract_text_from_pdf,
    convert_pdf_to_images,
    process_pdf
)

@pytest.mark.asyncio
async def test_load_pdf_from_source_bytes():
    pdf_bytes = b"%PDF-1.5 test content"
    result = await load_pdf_from_source(pdf_bytes)
    assert result == pdf_bytes

@pytest.mark.asyncio
async def test_load_pdf_from_source_local_file(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.5 file content")
    result = await load_pdf_from_source(pdf_path)
    assert result == b"%PDF-1.5 file content"

def test_check_pdf_has_text():
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = "This is a lot of text to pass the threshold of fifty characters."
    mock_doc.__len__.return_value = 1
    mock_doc.__getitem__.return_value = mock_page
    
    assert check_pdf_has_text(mock_doc) is True
    
    mock_page.get_text.return_value = "Short"
    assert check_pdf_has_text(mock_doc) is False

@pytest.mark.asyncio
async def test_extract_text_from_pdf():
    pdf_bytes = b"%PDF-1.5 content"
    
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Page text"
    mock_doc.__len__.return_value = 1
    mock_doc.__getitem__.return_value = mock_page
    
    with patch('fitz.open', return_value=mock_doc), \
         patch('app.modules.pdf_processor.load_pdf_from_source', new_callable=AsyncMock) as mock_load:
        mock_load.return_value = pdf_bytes
        
        result = await extract_text_from_pdf("test.pdf")
        assert result.total_pages == 1
        assert result.extracted_text == "Page text"
        assert result.method == "text"

@pytest.mark.asyncio
async def test_convert_pdf_to_images():
    pdf_bytes = b"%PDF-1.5 content"
    
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_pixmap = MagicMock()
    
    # Create a real small image for pixmap.tobytes
    img = Image.new('RGB', (10, 10))
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    mock_pixmap.tobytes.return_value = img_byte_arr.getvalue()
    
    mock_page.get_pixmap.return_value = mock_pixmap
    mock_doc.__len__.return_value = 1
    mock_doc.__getitem__.return_value = mock_page
    
    with patch('fitz.open', return_value=mock_doc), \
         patch('app.modules.pdf_processor.load_pdf_from_source', new_callable=AsyncMock) as mock_load:
        mock_load.return_value = pdf_bytes
        
        result = await convert_pdf_to_images("test.pdf")
        assert result.total_pages == 1
        assert len(result.pages) == 1
        assert "image" in result.pages[0]
        assert result.method == "image"

@pytest.mark.asyncio
async def test_process_pdf_decision():
    pdf_bytes = b"%PDF-1.5 content"
    
    with patch('app.modules.pdf_processor.load_pdf_from_source', new_callable=AsyncMock) as mock_load, \
         patch('fitz.open') as mock_fitz_open, \
         patch('app.modules.pdf_processor.check_pdf_has_text', return_value=True), \
         patch('app.modules.pdf_processor.extract_text_from_pdf', new_callable=AsyncMock) as mock_extract:
        
        mock_load.return_value = pdf_bytes
        
        await process_pdf("test.pdf")
        mock_extract.assert_called_once()

def test_check_pdf_has_text_variety():
    # 1. 测试只有一点点文本的情况 (应返回 False)
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Short"
    mock_doc.__len__.return_value = 1
    mock_doc.__getitem__.return_value = mock_page
    assert check_pdf_has_text(mock_doc, min_text_length=50) is False
    
    # 2. 测试第一页没文本但第二页有文本的情况 (应返回 True)
    mock_page1 = MagicMock()
    mock_page1.get_text.return_value = ""
    mock_page2 = MagicMock()
    mock_page2.get_text.return_value = "This is enough text to be detected as a text-based PDF document."
    
    def get_item(idx):
        return [mock_page1, mock_page2][idx]
        
    mock_doc.__len__.return_value = 2
    mock_doc.__getitem__.side_effect = get_item
    
    assert check_pdf_has_text(mock_doc, min_text_length=50) is True


