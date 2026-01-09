"""
Tests for Instructor-based metadata extractor (Native Gemini-mode).
"""
import pytest
from unittest.mock import Mock, patch
from app.modules.metadata_extractor import MetadataExtractor, extract_metadata

@pytest.fixture(autouse=True)
def reset_extractor():
    """Reset the global extractor instance before each test."""
    import app.modules.metadata_extractor
    app.modules.metadata_extractor._instance = None
    yield
    app.modules.metadata_extractor._instance = None

@pytest.fixture(autouse=True)
def mock_instructor_native():
    """Mock Instructor and Gemini SDK to avoid actual API calls."""
    with patch('app.modules.metadata_extractor.instructor.from_gemini') as mock_from_gemini, \
         patch('google.generativeai.configure') as mock_configure, \
         patch('google.generativeai.GenerativeModel') as mock_gen_model, \
         patch('app.modules.metadata_extractor.settings') as mock_settings:
        
        # Mock settings to have a dummy API key to avoid ValueError
        mock_settings.GEMINI_METADATA_API_KEY = "dummy_key"
        mock_settings.GEMINI_LLM_API_KEY = "dummy_key"
        mock_settings.GEMINI_METADATA_MODEL = "gemini-2.5-flash-preview-09-2025"
        mock_settings.GEMINI_LLM_MODEL = "gemini-2.5-flash-preview-09-2025"
        
        mock_client = Mock()
        mock_completions = Mock()
        mock_client.chat.completions = mock_completions
        mock_from_gemini.return_value = mock_client
        
        yield mock_completions

def test_extract_by_category_edu(mock_instructor_native):
    """Test EDU category metadata extraction."""
    mock_response = Mock()
    mock_response.model_dump.return_value = {
        "student_name": "John Doe",
        "institution": "UNIVERSITY OF SOUTHERN CALIFORNIA",
        "degree_type": "Master"
    }
    mock_instructor_native.create.return_value = mock_response
    
    text = "UNIVERSITY OF SOUTHERN CALIFORNIA Student Name: John Doe Degree: Master"
    metadata = extract_metadata(text, "EDU")
    
    assert metadata.get("student_name") == "John Doe"
    assert metadata.get("degree_type") == "Master"

def test_merge_with_llm_metadata(mock_instructor_native):
    """Test merging Instructor extraction with LLM results."""
    mock_response = Mock()
    mock_response.model_dump.return_value = {"tax_year": 2023}
    mock_instructor_native.create.return_value = mock_response
    
    llm_metadata = {"tax_year": 2024, "total_amount": 2000.0}
    metadata = extract_metadata("Tax Year: 2023", "TAX", llm_metadata=llm_metadata)
    
    assert metadata.get("tax_year") == 2023
    assert metadata.get("total_amount") == 2000.0

def test_schema_generation():
    """Test that schemas are dynamically generated from provided fields."""
    from app.modules.metadata_extractor import _get_schema_for_fields
    from pydantic import BaseModel
    
    fields = ["tax_year", "total_amount"]
    schema = _get_schema_for_fields("TAX", fields)
    assert issubclass(schema, BaseModel)
    assert "tax_year" in schema.model_fields
    assert "total_amount" in schema.model_fields
