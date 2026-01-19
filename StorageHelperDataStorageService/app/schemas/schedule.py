"""
Pydantic schemas for Schedule API
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any


class ScheduleCreate(BaseModel):
    """Schema for creating a schedule"""
    title: str
    event_type: Optional[str] = None
    description: Optional[str] = None
    scheduled_time: datetime
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    priority: int = 0
    metadata: Optional[Dict[str, Any]] = None


class ScheduleUpdate(BaseModel):
    """Schema for updating a schedule"""
    title: Optional[str] = None
    event_type: Optional[str] = None
    description: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class ScheduleStatusUpdate(BaseModel):
    """Schema for updating schedule status"""
    status: str  # pending, completed, cancelled


class ScheduleResponse(BaseModel):
    """Schema for schedule response"""
    id: int
    user_id: int
    title: str
    event_type: Optional[str] = None
    description: Optional[str] = None
    scheduled_time: datetime
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    status: str
    priority: int
    metadata: Optional[Dict[str, Any]] = Field(None, alias="extra_data")
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
