"""Schemas package - Pydantic request/response models (minimal)"""
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse, CategoryListRequest, CategoryListResponse
from app.schemas.location import LocationCreate, LocationUpdate, LocationResponse, LocationListRequest, LocationListResponse

__all__ = [
    "CategoryCreate",
    "CategoryUpdate",
    "CategoryResponse",
    "CategoryListRequest",
    "CategoryListResponse",
    "LocationCreate",
    "LocationUpdate",
    "LocationResponse",
    "LocationListRequest",
    "LocationListResponse",
]
