"""
Pydantic schemas for StorageLocation
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class LocationCreate(BaseModel):
    """Schema for creating a location"""
    name: str = Field(..., min_length=1, max_length=100, description="Location name (e.g., 'Bedroom desk, left drawer #2')")
    description: Optional[str] = Field(None, description="Location description")
    photo_url: Optional[str] = Field(None, description="URL to location photo")


class LocationUpdate(BaseModel):
    """Schema for updating a location"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Location name")
    description: Optional[str] = Field(None, description="Location description")
    photo_url: Optional[str] = Field(None, description="URL to location photo")


class LocationResponse(BaseModel):
    """Schema for location response"""
    id: int
    user_id: int
    name: str
    description: Optional[str] = None
    photo_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True  # ORM mode for Pydantic v2


class LocationListRequest(BaseModel):
    """Schema for bulk location creation/update request"""
    locations: list[LocationCreate] = Field(..., description="List of locations to create or update")


class LocationListResponse(BaseModel):
    """Schema for location list response"""
    user_id: int
    total: int
    locations: list[LocationResponse]

