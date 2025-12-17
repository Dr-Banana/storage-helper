"""
Document Output Schema
Unified management of document output structure and fields.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum


class OutputField(Enum):
    """Enumeration of available output fields."""
    # Basic fields
    STATUS = "status"
    OWNER_ID = "owner_id"
    SOURCE = "source"
    FILE_TYPE = "file_type"
    DOCUMENT_ID = "document_id"
    FILE_URL = "file_url"  # URL of file stored in database
    FILE_UPLOAD_ERROR = "file_upload_error"  # Error message if file upload failed
    PROCESSING_STEPS = "processing_steps"
    
    # OCR fields
    EXTRACTED_TEXT = "extracted_text"
    OCR_CONFIDENCE = "ocr_confidence"
    RAW_OCR_INFO = "raw_ocr_info"
    
    # Cleaning fields
    CLEANING_INFO = "cleaning_info"
    
    # Vision fields
    VISION_UNDERSTANDING = "vision_understanding"
    VISION_DESCRIPTION = "vision_understanding.description"
    VISION_CONFIDENCE = "vision_understanding.confidence"
    VISION_DETECTED_ELEMENTS = "vision_understanding.detected_elements"
    VISION_HAS_TEXT = "vision_understanding.has_text"
    
    # Recommendation fields
    RECOMMENDATION_STATUS = "recommendation_status"
    RECOMMENDATION_DATA = "recommendation_data"
    RECOMMENDATION_ERROR = "recommendation_error"
    
    # Embedding fields
    EMBEDDING = "embedding"
    EMBEDDING_DIMENSION = "embedding_dimension"
    EMBEDDING_STATUS = "embedding_status"
    
    # Error fields
    ERROR = "error"


@dataclass
class DocumentOutputSchema:
    """
    Unified schema for document output structure.
    Manages which fields should be included in the output and how they are structured.
    """
    
    # Field inclusion flags
    include_basic_fields: bool = True
    include_ocr_fields: bool = True
    include_cleaning_fields: bool = True
    include_vision_fields: bool = True
    include_recommendation_fields: bool = True
    include_embedding_fields: bool = True
    include_error_fields: bool = True
    
    # Field mappings (for renaming or transformation)
    field_mappings: Dict[str, str] = field(default_factory=dict)
    
    # Fields to exclude
    excluded_fields: List[str] = field(default_factory=list)
    
    def should_include(self, field: OutputField) -> bool:
        """
        Check if a field should be included in the output.
        
        :param field: The field to check
        :return: True if the field should be included
        """
        # Check if field is explicitly excluded
        if field.value in self.excluded_fields:
            return False
        
        # Check category inclusion flags
        if field in [OutputField.STATUS, OutputField.OWNER_ID, OutputField.SOURCE, 
                     OutputField.FILE_TYPE, OutputField.DOCUMENT_ID, OutputField.FILE_URL,
                     OutputField.FILE_UPLOAD_ERROR, OutputField.PROCESSING_STEPS]:
            return self.include_basic_fields
        
        if field in [OutputField.EXTRACTED_TEXT, OutputField.OCR_CONFIDENCE, OutputField.RAW_OCR_INFO]:
            return self.include_ocr_fields
        
        if field == OutputField.CLEANING_INFO:
            return self.include_cleaning_fields
        
        if field in [OutputField.VISION_UNDERSTANDING, OutputField.VISION_DESCRIPTION,
                     OutputField.VISION_CONFIDENCE, OutputField.VISION_DETECTED_ELEMENTS,
                     OutputField.VISION_HAS_TEXT]:
            return self.include_vision_fields
        
        if field in [OutputField.RECOMMENDATION_STATUS, OutputField.RECOMMENDATION_DATA,
                     OutputField.RECOMMENDATION_ERROR]:
            return self.include_recommendation_fields
        
        if field in [OutputField.EMBEDDING, OutputField.EMBEDDING_DIMENSION, OutputField.EMBEDDING_STATUS]:
            return self.include_embedding_fields
        
        if field == OutputField.ERROR:
            return self.include_error_fields
        
        return True
    
    def get_field_name(self, field: OutputField) -> str:
        """
        Get the output field name, applying any mappings.
        
        :param field: The field to get the name for
        :return: The field name to use in output
        """
        if field.value in self.field_mappings:
            return self.field_mappings[field.value]
        return field.value
    
    def build_output(
        self,
        status: str,
        owner_id: int,
        source: str,
        file_type: Optional[str],
        document_id: Optional[str],
        processing_steps: List[str],
        file_url: Optional[str] = None,
        file_upload_error: Optional[str] = None,
        extracted_text: Optional[str] = None,
        ocr_confidence: Optional[float] = None,
        raw_ocr_info: Optional[Dict[str, Any]] = None,
        cleaning_info: Optional[Dict[str, Any]] = None,
        vision_understanding: Optional[Dict[str, Any]] = None,
        recommendation_status: Optional[str] = None,
        recommendation_data: Optional[Dict[str, Any]] = None,
        recommendation_error: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        embedding_dimension: Optional[int] = None,
        embedding_status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build output dictionary based on schema configuration.
        
        :param status: Pipeline status
        :param owner_id: Owner ID
        :param source: Source image URL
        :param file_type: File type ("image" or "pdf")
        :param document_id: Document ID
        :param file_url: URL of file stored in database (from upload/process API)
        :param file_upload_error: Error message if file upload failed (AI processing succeeded)
        :param processing_steps: List of processing steps completed
        :param extracted_text: Extracted text from OCR
        :param ocr_confidence: OCR confidence score
        :param raw_ocr_info: Raw OCR information
        :param cleaning_info: Text cleaning information
        :param vision_understanding: Vision analysis results
        :param recommendation_status: Recommendation status
        :param recommendation_data: Recommendation data
        :param recommendation_error: Recommendation error message
        :param embedding: Embedding vector
        :param embedding_dimension: Embedding dimension
        :param embedding_status: Embedding status
        :param error: Error message
        :return: Output dictionary
        """
        output: Dict[str, Any] = {}
        
        # Basic fields
        if self.should_include(OutputField.STATUS):
            output[self.get_field_name(OutputField.STATUS)] = status
        if self.should_include(OutputField.OWNER_ID):
            output[self.get_field_name(OutputField.OWNER_ID)] = owner_id
        if self.should_include(OutputField.SOURCE):
            output[self.get_field_name(OutputField.SOURCE)] = source
        if self.should_include(OutputField.FILE_TYPE):
            output[self.get_field_name(OutputField.FILE_TYPE)] = file_type or "image"
        if self.should_include(OutputField.DOCUMENT_ID):
            # Keep document_id as int (or convert from string if needed)
            if document_id is not None:
                if isinstance(document_id, str):
                    try:
                        output[self.get_field_name(OutputField.DOCUMENT_ID)] = int(document_id)
                    except (ValueError, TypeError):
                        output[self.get_field_name(OutputField.DOCUMENT_ID)] = document_id
                else:
                    output[self.get_field_name(OutputField.DOCUMENT_ID)] = document_id
            else:
                output[self.get_field_name(OutputField.DOCUMENT_ID)] = None
        if self.should_include(OutputField.FILE_URL) and file_url:
            output[self.get_field_name(OutputField.FILE_URL)] = file_url
        if self.should_include(OutputField.FILE_UPLOAD_ERROR) and file_upload_error:
            output[self.get_field_name(OutputField.FILE_UPLOAD_ERROR)] = file_upload_error
        if self.should_include(OutputField.PROCESSING_STEPS):
            output[self.get_field_name(OutputField.PROCESSING_STEPS)] = processing_steps.copy()
        
        # OCR fields
        if self.should_include(OutputField.EXTRACTED_TEXT) and extracted_text:
            output[self.get_field_name(OutputField.EXTRACTED_TEXT)] = extracted_text
        if self.should_include(OutputField.OCR_CONFIDENCE) and ocr_confidence is not None:
            output[self.get_field_name(OutputField.OCR_CONFIDENCE)] = ocr_confidence
        if self.should_include(OutputField.RAW_OCR_INFO) and raw_ocr_info:
            output[self.get_field_name(OutputField.RAW_OCR_INFO)] = raw_ocr_info
        
        # Cleaning fields
        if self.should_include(OutputField.CLEANING_INFO) and cleaning_info:
            output[self.get_field_name(OutputField.CLEANING_INFO)] = cleaning_info
        
        # Vision fields
        if self.should_include(OutputField.VISION_UNDERSTANDING) and vision_understanding:
            output[self.get_field_name(OutputField.VISION_UNDERSTANDING)] = vision_understanding
        
        # Recommendation fields
        if self.should_include(OutputField.RECOMMENDATION_STATUS) and recommendation_status:
            output[self.get_field_name(OutputField.RECOMMENDATION_STATUS)] = recommendation_status
        if self.should_include(OutputField.RECOMMENDATION_DATA) and recommendation_data:
            output[self.get_field_name(OutputField.RECOMMENDATION_DATA)] = recommendation_data
        if self.should_include(OutputField.RECOMMENDATION_ERROR) and recommendation_error:
            output[self.get_field_name(OutputField.RECOMMENDATION_ERROR)] = recommendation_error
        
        # Embedding fields
        if self.should_include(OutputField.EMBEDDING) and embedding:
            output[self.get_field_name(OutputField.EMBEDDING)] = embedding
        if self.should_include(OutputField.EMBEDDING_DIMENSION) and embedding_dimension:
            output[self.get_field_name(OutputField.EMBEDDING_DIMENSION)] = embedding_dimension
        if self.should_include(OutputField.EMBEDDING_STATUS) and embedding_status:
            output[self.get_field_name(OutputField.EMBEDDING_STATUS)] = embedding_status
        
        # Error fields
        if self.should_include(OutputField.ERROR) and error:
            output[self.get_field_name(OutputField.ERROR)] = error
        
        return output


# Default schema instance (includes all fields)
default_output_schema = DocumentOutputSchema()

