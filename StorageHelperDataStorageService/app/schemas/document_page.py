"""
Pydantic schemas for DocumentPage
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DocumentPageCreate(BaseModel):
    """Schema for creating a document page"""
    document_id: int = Field(..., description="Document ID")
    page_number: int = Field(..., ge=1, description="Page number (1-indexed)")
    image_url: str = Field(..., description="URL or path to page image")
    ocr_text: Optional[str] = Field(None, description="Extracted text from OCR for this page")


class DocumentPageUpdate(BaseModel):
    """Schema for updating a document page"""
    page_number: Optional[int] = Field(None, ge=1, description="Page number (1-indexed)")
    image_url: Optional[str] = Field(None, description="URL or path to page image")
    ocr_text: Optional[str] = Field(None, description="Extracted text from OCR for this page")


class DocumentPageResponse(BaseModel):
    """Schema for document page response"""
    id: int
    document_id: int
    page_number: int
    image_url: str
    ocr_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
