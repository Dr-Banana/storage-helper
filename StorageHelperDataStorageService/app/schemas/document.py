"""
Pydantic schemas for Document
"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    """Schema for creating a document"""
    title: Optional[str] = Field(None, max_length=255, description="Document title")
    category_id: Optional[int] = Field(None, description="Document category ID")
    owner_id: int = Field(..., description="Document owner (user) ID")
    event_id: Optional[int] = Field(None, description="Associated event ID")
    current_location_id: Optional[int] = Field(None, description="Current storage location ID")
    image_url: Optional[str] = Field(None, description="URL or path to thumbnail/first page")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Flexible metadata")


class DocumentUpdate(BaseModel):
    """Schema for updating a document"""
    title: Optional[str] = Field(None, max_length=255, description="Document title")
    category_id: Optional[int] = Field(None, description="Document category ID")
    event_id: Optional[int] = Field(None, description="Associated event ID")
    current_location_id: Optional[int] = Field(None, description="Current storage location ID")
    image_url: Optional[str] = Field(None, description="URL or path to thumbnail/first page")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Flexible metadata")


class DocumentResponse(BaseModel):
    """Schema for document response"""
    id: int
    title: Optional[str] = None
    category_id: Optional[int] = None
    owner_id: int
    event_id: Optional[int] = None
    current_location_id: Optional[int] = None
    image_url: Optional[str] = None
    doc_metadata: Optional[Dict[str, Any]] = Field(None, validation_alias="doc_metadata", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True  # Allow both 'doc_metadata' and 'metadata'


class DocumentWithPagesResponse(BaseModel):
    """Schema for document response including all pages"""
    id: int
    title: Optional[str] = None
    category_id: Optional[int] = None
    owner_id: int
    event_id: Optional[int] = None
    current_location_id: Optional[int] = None
    image_url: Optional[str] = None
    doc_metadata: Optional[Dict[str, Any]] = Field(None, validation_alias="doc_metadata", serialization_alias="metadata")
    pages: Optional[list] = Field(None, description="List of document pages")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class DocumentListResponse(BaseModel):
    """Schema for list of documents"""
    total: int = Field(..., description="Total number of documents")
    documents: list[DocumentResponse] = Field(..., description="List of documents")


class DocumentSearchRequest(BaseModel):
    """Schema for semantic search request"""
    embedding: list[float] = Field(..., description="768-dimensional query vector")
    user_id: int = Field(..., description="ID of the user whose documents to search")
    top_k: int = Field(5, description="Number of results to return")
    exclude_receipts: bool = Field(False, description="If True, exclude receipt parent documents and only return items")
