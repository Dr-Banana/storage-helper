"""
Pydantic schemas for User
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, EmailStr, field_validator

CookingLevel = Literal["beginner", "intermediate", "expert"]
UserLanguage = Literal["zh", "en", "ja", "ko"]


class UserCreate(BaseModel):
    """Schema for creating a user"""
    google_id: str = Field(..., description="Google OAuth ID")
    email: EmailStr = Field(..., description="User email")
    display_name: str = Field(..., min_length=1, max_length=100, description="User display name")
    note: Optional[str] = Field(None, max_length=1000, description="Optional user note")
    cooking_level: CookingLevel = Field("beginner", description="User cooking skill level")
    language: UserLanguage = Field("zh", description="Preferred language for AI responses")
    default_servings: int = Field(1, ge=1, le=20, description="Default number of servings to generate")
    meat_veg_ratio: str = Field("1:1:1", description="Dish count ratio: meat:veg:staple (e.g. '1:1:1')")
    include_soup: bool = Field(True, description="Whether soup must be included in every meal plan")
    calorie_target: Optional[int] = Field(None, ge=100, le=5000, description="Optional per-meal calorie target in kcal")
    disliked_ingredients: Optional[List[str]] = Field(default_factory=list, description="Ingredients the user dislikes or avoids")
    cuisine_weights: Optional[Dict[str, int]] = Field(None, description="Cuisine region weights for variety engine, e.g. {'Chinese': 50, 'Western': 20}")
    recent_dishes: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Recent dishes eaten, used for recency penalty")


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    display_name: Optional[str] = Field(None, min_length=1, max_length=100, description="User display name")
    note: Optional[str] = Field(None, max_length=1000, description="Optional user note")
    cooking_level: Optional[CookingLevel] = Field(None, description="User cooking skill level")
    language: Optional[UserLanguage] = Field(None, description="Preferred language for AI responses")
    default_servings: Optional[int] = Field(None, ge=1, le=20, description="Default number of servings to generate")
    meat_veg_ratio: Optional[str] = Field(None, description="Dish count ratio: meat:veg:staple (e.g. '1:1:1')")
    include_soup: Optional[bool] = Field(None, description="Whether soup must be included in every meal plan")
    calorie_target: Optional[int] = Field(None, ge=100, le=5000, description="Optional per-meal calorie target in kcal")
    disliked_ingredients: Optional[List[str]] = Field(None, description="Ingredients the user dislikes or avoids")
    cuisine_weights: Optional[Dict[str, int]] = Field(None, description="Cuisine region weights, e.g. {'Chinese': 50, 'Western': 20}")
    recent_dishes: Optional[List[Dict[str, Any]]] = Field(None, description="Recent dishes eaten, used for recency penalty")


class UserResponse(BaseModel):
    """Schema for user response"""
    id: int
    google_id: str
    email: str
    display_name: str
    note: Optional[str] = None
    cooking_level: CookingLevel = "beginner"
    language: UserLanguage = "zh"
    default_servings: int = 1
    meat_veg_ratio: str = "1:1:1"
    include_soup: bool = True
    calorie_target: Optional[int] = None
    disliked_ingredients: Optional[List[str]] = Field(default_factory=list)
    cuisine_weights: Optional[Dict[str, int]] = Field(default_factory=lambda: {"Chinese": 50, "Western": 20, "Japanese": 15, "Korean": 10, "Other": 5})
    recent_dishes: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    is_premium: bool = False
    premium_expiry: Optional[datetime] = None
    premium_source: Optional[str] = None
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
