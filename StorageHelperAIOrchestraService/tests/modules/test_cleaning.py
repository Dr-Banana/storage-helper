import pytest
from app.modules.cleaning import clean_ocr_text, filter_low_confidence_text, process_text

def test_clean_ocr_text_basic():
    text = "  Hello   World  \n\n  This is a   test.  "
    expected = "Hello World This is a test."
    result = clean_ocr_text(text)
    assert result == expected

def test_clean_ocr_text_empty():
    assert clean_ocr_text("") == ""
    assert clean_ocr_text(None) == ""

def test_clean_ocr_text_ocr_fixes():
    text = "rn vv"
    result = clean_ocr_text(text)
    assert result == "m w"

def test_clean_ocr_text_garbage_removal():
    text = "Valid line\n!@#$%^&*()_+\nAnother valid line"
    result = clean_ocr_text(text)
    assert "Valid line" in result
    assert "!@#$%^&*()_+" not in result
    assert "Another valid line" in result

def test_filter_low_confidence_text():
    ocr_data = {
        'text': ['Hello', 'World', 'Garbage'],
        'conf': [90, 80, 10]
    }
    result = filter_low_confidence_text(ocr_data, min_confidence=50)
    assert result == "Hello World"

def test_filter_low_confidence_text_empty():
    assert filter_low_confidence_text({}) == ""
    assert filter_low_confidence_text({'text': []}) == ""

@pytest.mark.asyncio
async def test_process_text_basic():
    ocr_text = "Raw OCR Text"
    result = await process_text(ocr_text)
    assert result["original_text"] == ocr_text
    assert result["cleaned_text"] == "Raw OCR Text"
    assert result["cleaning_applied"] is True

@pytest.mark.asyncio
async def test_process_text_with_confidence():
    ocr_text = "Raw OCR Text"
    ocr_data = {
        'text': ['Raw', 'OCR', 'Text', 'LowConf'],
        'conf': [90, 90, 90, 10]
    }
    result = await process_text(ocr_text, ocr_data=ocr_data, min_confidence=50)
    assert result["cleaned_text"] == "Raw OCR Text"
