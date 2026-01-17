"""
Location Image Management API

This module provides endpoints for managing location images.
Allows associating image URLs with storage locations.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional, Annotated
from pydantic import BaseModel, Field, HttpUrl
from io import BytesIO
import urllib.parse

from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.core.auth_decorators import get_location_if_owner
from app.models.storage_location import StorageLocation
from app.schemas.location import LocationResponse
from app.integrations import StorageClient

router = APIRouter(prefix="/api/locations", tags=["location-images"])


# ============================================================
# Request/Response Schemas
# ============================================================

class LocationImageAssignRequest(BaseModel):
    """Schema for assigning an image to a location"""
    location_id: int = Field(..., description="Storage location ID")
    image_url: str = Field(..., description="URL of the image to assign to the location")
    user_id: int = Field(..., description="User ID (owner of the location)")


class LocationImageAssignResponse(BaseModel):
    """Schema for location image assignment response"""
    id: int
    user_id: int
    name: str
    description: Optional[str] = None
    photo_url: Optional[str] = None
    message: str

    class Config:
        from_attributes = True


class LocationImageUploadResponse(BaseModel):
    """Schema for location image upload response"""
    image_url: str = Field(..., description="Path where the image was stored")
    filename: str = Field(..., description="Original filename")


# ============================================================
# Location Image Upload Endpoints
# ============================================================

@router.post(
    "/upload-image",
    response_model=LocationImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload location image to storage",
    description="""
    Upload a location photo image to storage backend.
    Returns image_url for use when creating/updating a location.
    
    No database operations - purely file storage.
    """
)
def upload_location_image(
    file: UploadFile = File(..., description="Location photo image file"),
    owner_id: int = Form(..., description="Location owner user ID"),
):
    """
    Upload location image to storage.
    
    - **file**: Location photo image file (JPG, PNG, etc.)
    - **owner_id**: Location owner user ID
    
    Returns:
    - image_url: API endpoint URL to serve the image
    - filename: Original filename
    """
    try:
        # Read file content
        file_content = BytesIO(file.file.read())
        
        # Upload to storage in locations folder
        file_path = StorageClient.upload_image(
            file_content=file_content,
            filename=file.filename,
            folder=f"locations/{owner_id}"
        )
        
        # Return the file path directly (without conversion to API URL)
        # The caller can decide whether to store it as-is or convert it
        return LocationImageUploadResponse(
            image_url=file_path,
            filename=file.filename
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload location image: {str(e)}"
        )


# ============================================================
# Location Image Assignment Endpoints
# ============================================================

@router.post(
    "/assign-image",
    response_model=LocationImageAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign image to a storage location",
    description="""
    Assign an image URL to a storage location.
    This updates the photo_url field of the location.
    
    - **location_id**: ID of the storage location
    - **image_url**: URL of the image to assign
    - **user_id**: User ID (owner of the location)
    """
)
def assign_image_to_location(
    request: LocationImageAssignRequest,
    db: Session = Depends(get_db)
):
    """
    Assign an image URL to a storage location.
    
    - **request**: LocationImageAssignRequest containing location_id, image_url, and user_id
    
    Returns updated LocationResponse with the new image_url
    
    Raises:
    - 404: Location not found
    - 403: User not authorized to update this location
    - 400: Invalid image URL
    """
    try:
        # Fetch the location
        location = db.query(StorageLocation).filter(
            StorageLocation.id == request.location_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Storage location with id {request.location_id} not found"
            )
        
        # Verify user ownership
        if location.user_id != request.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not authorized to update this location"
            )
        
        # Validate image URL format
        if not request.image_url.startswith(('http://', 'https://', '/')):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid image URL format. Must start with http://, https://, or /"
            )
        
        # Update the location's photo_url
        location.photo_url = request.image_url
        db.commit()
        db.refresh(location)
        
        return LocationImageAssignResponse(
            id=location.id,
            user_id=location.user_id,
            name=location.name,
            description=location.description,
            photo_url=location.photo_url,
            message=f"Successfully assigned image to location '{location.name}'"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to assign image to location: {str(e)}"
        )


@router.put(
    "/{location_id}/image",
    response_model=LocationImageAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Update location image by location ID",
    description="""
    Update the image (photo_url) of a storage location.
    
    - **location_id**: ID of the storage location (path parameter)
    - **image_url**: New image URL (query parameter)
    - **user_id**: User ID (owner of the location, query parameter)
    """
)
def update_location_image(
    location_id: int,
    image_url: str = Query(..., description="New image URL"),
    user_id: int = Query(..., description="User ID (owner of the location)"),
    db: Session = Depends(get_db)
):
    """
    Update the image URL of a storage location.
    
    - **location_id**: ID of the storage location
    - **image_url**: New image URL
    - **user_id**: User ID (owner of the location)
    
    Returns updated LocationResponse with the new image_url
    
    Raises:
    - 404: Location not found
    - 403: User not authorized to update this location
    - 400: Invalid image URL
    """
    try:
        # Fetch the location
        location = db.query(StorageLocation).filter(
            StorageLocation.id == location_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Storage location with id {location_id} not found"
            )
        
        # Verify user ownership
        if location.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not authorized to update this location"
            )
        
        # Validate image URL format
        if not image_url.startswith(('http://', 'https://', '/')):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid image URL format. Must start with http://, https://, or /"
            )
        
        # Update the location's photo_url
        location.photo_url = image_url
        db.commit()
        db.refresh(location)
        
        return LocationImageAssignResponse(
            id=location.id,
            user_id=location.user_id,
            name=location.name,
            description=location.description,
            photo_url=location.photo_url,
            message=f"Successfully updated image for location '{location.name}'"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update location image: {str(e)}"
        )


@router.get(
    "/{location_id}/image",
    response_model=LocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get location image details",
    description="""
    Retrieve the image URL and details of a storage location.
    
    - **location_id**: ID of the storage location (path parameter)
    """
)
def get_location_image(
    location: Annotated[StorageLocation, Depends(get_location_if_owner)]
):
    """
    Get the image URL and details of a storage location.
    
    - **location_id**: ID of the storage location
    
    Returns LocationResponse with the image_url
    
    Authorization: User must own the location (verified by dependency)
    
    Raises:
    - 404: Location not found
    - 403: User not authorized to view this location
    """
    try:
        return LocationResponse(
            id=location.id,
            user_id=location.user_id,
            name=location.name,
            description=location.description,
            photo_url=location.photo_url,
            created_at=location.created_at,
            updated_at=location.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve location image: {str(e)}"
        )
