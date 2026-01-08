import pytest
from app.storage.output_schema import DocumentOutputSchema, OutputField, default_output_schema

def test_document_output_schema_basic():
    schema = DocumentOutputSchema()
    output = schema.build_output(
        status="completed",
        owner_id=1,
        source="http://test/img.jpg",
        file_type="image",
        document_id=123,
        processing_steps=["OCR"]
    )
    
    assert output["status"] == "completed"
    assert output["owner_id"] == 1
    assert output["document_id"] == 123
    assert output["processing_steps"] == ["OCR"]

def test_document_output_schema_exclusion():
    schema = DocumentOutputSchema(include_ocr_fields=False)
    output = schema.build_output(
        status="completed",
        owner_id=1,
        source="http://test/img.jpg",
        file_type="image",
        document_id=123,
        processing_steps=["OCR"],
        extracted_text="Some text"
    )
    
    assert "extracted_text" not in output
    assert output["status"] == "completed"

def test_document_output_schema_mapping():
    schema = DocumentOutputSchema(field_mappings={"status": "pipeline_status"})
    output = schema.build_output(
        status="completed",
        owner_id=1,
        source="http://test/img.jpg",
        file_type="image",
        document_id=123,
        processing_steps=["OCR"]
    )
    
    assert "status" not in output
    assert output["pipeline_status"] == "completed"

def test_document_output_schema_should_include():
    schema = DocumentOutputSchema(excluded_fields=["extracted_text"])
    assert schema.should_include(OutputField.STATUS) is True
    assert schema.should_include(OutputField.EXTRACTED_TEXT) is False

