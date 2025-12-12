"""
Pydantic schemas for DocumentCategory
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    """Schema for creating a category"""
    code: str = Field(..., min_length=1, max_length=50, description="Category code (e.g., 'TAX', 'VISA', 'MED')")
    name: str = Field(..., min_length=1, max_length=100, description="Category display name")
    description: Optional[str] = Field(None, description="Category description")
    classification: Optional[str] = Field(None, description="Category classification (e.g., virtual/physical)")


class CategoryUpdate(BaseModel):
    """Schema for updating a category"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Category display name")
    description: Optional[str] = Field(None, description="Category description")
    classification: Optional[str] = Field(None, description="Category classification")


class CategoryResponse(BaseModel):
    """Schema for category response"""
    id: int
    user_id: int
    code: str
    name: str
    description: Optional[str] = None
    classification: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True  # ORM mode for Pydantic v2


class CategoryListRequest(BaseModel):
    """Schema for bulk category creation/update request"""
    categories: list[CategoryCreate] = Field(..., description="List of categories to create or update")


class CategoryListResponse(BaseModel):
    """Schema for category list response"""
    user_id: int
    total: int
    categories: list[CategoryResponse]

