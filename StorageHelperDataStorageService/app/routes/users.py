"""
User management routes
"""
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status, Path
from typing import List, Annotated
import urllib.parse

from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.core.auth_decorators import get_document_if_owner
from app.services.user_service import UserService
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListResponse
from app.schemas.category import CategoryCreate
from app.schemas.location import LocationCreate, LocationUpdate
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["users"])


class EmptyResponse(BaseModel):
    """Empty response model"""
    pass


def _convert_to_accessible_url(file_path: str) -> str:
    """
    Convert local file path to accessible HTTP URL.
    If already a URL (http/https), return as is.
    If local file path, convert to API endpoint URL.
    """
    # If already a URL, return as is
    if file_path and file_path.startswith(('http://', 'https://')):
        return file_path
    
    # If local file path, convert to API endpoint URL
    if file_path:
        encoded_path = urllib.parse.quote(file_path, safe='')
        return f"/api/documents/files?path={encoded_path}"
    
    return None


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
    description="Create a new user in the system"
)
def create_user(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Create a new user
    
    - **display_name**: User's display name (required, 1-100 characters)
    - **note**: Optional note about the user
    """
    try:
        return UserService.create_user(db, user_data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user: {str(e)}"
        )


@router.get(
    "",
    response_model=UserListResponse,
    summary="Get all users",
    description="Retrieve all users in the system"
)
def get_users(db: Session = Depends(get_db)):
    """
    Get all users
    
    Returns a list of all users and the total count
    """
    try:
        users = UserService.get_all_users(db)
        return UserListResponse(
            total=len(users),
            users=users
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}"
        )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID",
    description="Retrieve a specific user by their ID"
)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """
    Get a specific user by ID
    
    - **user_id**: The user's ID
    """
    try:
        user = UserService.get_user_by_id(db, user_id)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        return user
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user: {str(e)}"
        )


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user",
    description="Update a specific user's information"
)
def update_user(user_id: int, user_data: UserUpdate, db: Session = Depends(get_db)):
    """
    Update a specific user
    
    - **user_id**: The user's ID
    - **user_data**: Fields to update (all optional)
    """
    try:
        return UserService.update_user(db, user_id, user_data)
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user: {str(e)}"
        )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user",
    description="Delete a specific user"
)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """
    Delete a specific user
    
    - **user_id**: The user's ID
    """
    try:
        UserService.delete_user(db, user_id)
        return None
    except ValueError as e:
        if "not found" in str(e):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(e)}"
        )


@router.get(
    "/{user_id}/documents",
    response_model=dict,
    summary="Get all document IDs for a user",
    description="Retrieve all document IDs owned by a specific user"
)
def get_user_documents(user_id: int, db: Session = Depends(get_db)):
    """
    Get all document IDs for a specific user
    
    - **user_id**: The user's ID
    
    Returns a list of document IDs owned by the user
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        # Get all documents for the user
        from app.models.document import Document
        documents = db.query(Document.id).filter(Document.owner_id == user_id).all()
        document_ids = [doc.id for doc in documents]
        
        return {
            "user_id": user_id,
            "total": len(document_ids),
            "document_ids": document_ids
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user documents: {str(e)}"
        )


@router.get(
    "/{user_id}/categories",
    response_model=dict,
    summary="Get all categories for a user's documents",
    description="Retrieve all unique document categories owned by a specific user"
)
def get_user_categories(user_id: int, db: Session = Depends(get_db)):
    """
    Get all unique document categories for a user's documents
    
    - **user_id**: The user's ID
    
    Returns a list of unique categories for the user's documents
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        # Get all categories for the user
        from app.models.document_category import DocumentCategory
        
        categories = db.query(DocumentCategory).filter(
            DocumentCategory.user_id == user_id
        ).all()
        
        return {
            "user_id": user_id,
            "total": len(categories),
            "categories": [
                {"id": c.id, "code": c.code, "name": c.name, "description": c.description}
                for c in categories
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user categories: {str(e)}"
        )


@router.get(
    "/{user_id}/locations",
    response_model=dict,
    summary="Get all storage locations for a user's documents",
    description="Retrieve all unique storage locations where a user's documents are stored"
)
def get_user_locations(user_id: int, db: Session = Depends(get_db)):
    """
    Get all unique storage locations for a user's documents
    
    - **user_id**: The user's ID
    
    Returns a list of unique storage locations for the user's documents
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        # Get all storage locations for the user
        from app.models.storage_location import StorageLocation
        
        locations = db.query(StorageLocation).filter(
            StorageLocation.user_id == user_id
        ).all()
        
        return {
            "user_id": user_id,
            "total": len(locations),
            "locations": [
                {
                    "id": loc.id,
                    "name": loc.name,
                    "description": loc.description,
                    "photo_url": _convert_to_accessible_url(loc.photo_url) if loc.photo_url else None
                }
                for loc in locations
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user locations: {str(e)}"
        )


@router.post(
    "/{user_id}/categories",
    response_model=EmptyResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a new category for a user",
    description="Create a new document category for a specific user"
)
def create_user_category(
    user_id: Annotated[int, Path()],
    category_data: CategoryCreate,
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    db: Session = Depends(get_db)
) -> dict:
    """
    Create a new category for a specific user
    
    - **user_id**: The user's ID
    - **category_data**: Category information (code, name, description, classification)
    
    Returns nothing on success, error message on failure
    
    Authorization: user_id must match current user
    """
    # Verify user_id matches current user
    if user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot perform this action for other users"
        )
    
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        from app.models.document_category import DocumentCategory
        
        # Check if category with this code already exists for this user
        existing_category = db.query(DocumentCategory).filter(
            DocumentCategory.user_id == user_id,
            DocumentCategory.code == category_data.code
        ).first()
        
        if existing_category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category with code '{category_data.code}' already exists for user {user_id}"
            )
        
        # Create new category
        new_category = DocumentCategory(
            user_id=user_id,
            code=category_data.code,
            name=category_data.name,
            description=category_data.description,
            classification=category_data.classification
        )
        db.add(new_category)
        db.commit()
        
        return {}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user category: {str(e)}"
        )


@router.post(
    "/{user_id}/locations",
    response_model=EmptyResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a new location for a user",
    description="Create a new storage location for a specific user"
)
def create_user_location(
    user_id: int,
    location_data: LocationCreate,
    db: Session = Depends(get_db)
) -> dict:
    """
    Create a new storage location for a specific user
    
    - **user_id**: The user's ID
    - **location_data**: Location information (name, description, photo_url)
    
    Returns nothing on success, error message on failure
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        from app.models.storage_location import StorageLocation
        
        # Create new location
        new_location = StorageLocation(
            user_id=user_id,
            name=location_data.name,
            description=location_data.description,
            photo_url=location_data.photo_url
        )
        db.add(new_location)
        db.commit()
        
        return {}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user location: {str(e)}"
        )


@router.put(
    "/{user_id}/locations/{location_id}",
    response_model=EmptyResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a location for a user",
    description="Update an existing storage location for a specific user"
)
def update_user_location(
    user_id: int,
    location_id: int,
    location_data: LocationUpdate,
    db: Session = Depends(get_db)
) -> dict:
    """
    Update an existing storage location for a specific user
    
    - **user_id**: The user's ID
    - **location_id**: The location's ID to update
    - **location_data**: Updated location information (name, description, photo_url)
    
    Returns nothing on success, error message on failure
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        from app.models.storage_location import StorageLocation
        
        # Get the location
        location = db.query(StorageLocation).filter(
            StorageLocation.id == location_id,
            StorageLocation.user_id == user_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location with ID {location_id} not found for user {user_id}"
            )
        
        # Update location fields (only update provided fields)
        if location_data.name is not None:
            location.name = location_data.name
        if location_data.description is not None:
            location.description = location_data.description
        if location_data.photo_url is not None:
            location.photo_url = location_data.photo_url
        
        db.commit()
        
        return {}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user location: {str(e)}"
        )


@router.get(
    "/{user_id}/locations/{location_id}/documents",
    response_model=dict,
    summary="Get all documents in a location for a user",
    description="Retrieve all document IDs stored in a specific location for a specific user"
)
def get_user_location_documents(
    user_id: int,
    location_id: int,
    db: Session = Depends(get_db)
):
    """
    Get all document IDs stored in a specific location for a specific user
    
    - **user_id**: The user's ID
    - **location_id**: The location's ID
    
    Returns a list of document IDs stored in this location for the user
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        from app.models.storage_location import StorageLocation
        from app.models.document import Document
        
        # Get the location and verify it belongs to the user
        location = db.query(StorageLocation).filter(
            StorageLocation.id == location_id,
            StorageLocation.user_id == user_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location with ID {location_id} not found for user {user_id}"
            )
        
        # Get all documents in this location for this user
        documents = db.query(Document).filter(
            Document.current_location_id == location_id,
            Document.owner_id == user_id
        ).all()
        
        document_ids = [doc.id for doc in documents]
        
        return {
            "user_id": user_id,
            "location_id": location_id,
            "total": len(document_ids),
            "document_ids": document_ids
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve documents for location: {str(e)}"
        )


@router.delete(
    "/{user_id}/locations/{location_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a location for a user",
    description="Delete an existing storage location for a specific user. Cannot delete if location contains documents."
)
def delete_user_location(
    user_id: int,
    location_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete an existing storage location for a specific user
    
    - **user_id**: The user's ID
    - **location_id**: The location's ID to delete
    
    Note: This operation is prohibited if the location contains any documents. 
    All documents must be moved out of the location before deletion.
    """
    try:
        # Verify user exists
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {user_id} not found"
            )
        
        from app.models.storage_location import StorageLocation
        from app.models.document import Document
        
        # Get the location
        location = db.query(StorageLocation).filter(
            StorageLocation.id == location_id,
            StorageLocation.user_id == user_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Location with ID {location_id} not found for user {user_id}"
            )
        
        # Check if there are any documents in this location
        documents_count = db.query(Document).filter(
            Document.current_location_id == location_id,
            Document.owner_id == user_id
        ).count()
        
        if documents_count > 0:
            # Get document IDs for error message (limit to 10 for response)
            documents = db.query(Document.id).filter(
                Document.current_location_id == location_id,
                Document.owner_id == user_id
            ).limit(10).all()
            document_ids = [doc.id for doc in documents]
            
            # Prepare error message
            error_detail = {
                "error": "Cannot delete location that contains documents",
                "message": f"Location {location_id} contains {documents_count} document(s). Please move all documents out of this location before deleting it.",
                "location_id": location_id,
                "document_count": documents_count,
                "sample_document_ids": document_ids[:10] if documents_count > 10 else document_ids
            }
            if documents_count > 10:
                error_detail["note"] = f"Showing first 10 of {documents_count} documents"
            
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_detail
            )
        
        # Delete the location (safe to delete as no documents use it)
        db.delete(location)
        db.commit()
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user location: {str(e)}"
        )
