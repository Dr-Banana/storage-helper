"""
Pydantic schemas for User
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr


class UserCreate(BaseModel):
    """Schema for creating a user"""
    google_id: str = Field(..., description="Google OAuth ID")
    email: EmailStr = Field(..., description="User email")
    display_name: str = Field(..., min_length=1, max_length=100, description="User display name")
    note: Optional[str] = Field(None, max_length=1000, description="Optional user note")


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    display_name: Optional[str] = Field(None, min_length=1, max_length=100, description="User display name")
    note: Optional[str] = Field(None, max_length=1000, description="Optional user note")


class UserResponse(BaseModel):
    """Schema for user response"""
    id: int
    google_id: str
    email: str
    display_name: str
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True  # ORM mode for Pydantic v2


class UserListResponse(BaseModel):
    """Schema for list of users"""
    total: int = Field(..., description="Total number of users")
    users: list[UserResponse] = Field(..., description="List of users")


# Google OAuth related schemas
class GoogleTokenRequest(BaseModel):
    """Schema for Google token validation request"""
    token: str = Field(..., description="Google ID token from client")


class GoogleAuthResponse(BaseModel):
    """Schema for Google authentication response"""
    user_id: int = Field(..., description="System user ID (assigned or existing)")
    is_new_user: bool = Field(..., description="Whether this is a newly created user")
    email: str = Field(..., description="User email")
    display_name: str = Field(..., description="User display name")
    auth_token: str = Field(..., description="Authentication token for API requests")
