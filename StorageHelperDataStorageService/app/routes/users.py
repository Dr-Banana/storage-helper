"""
User management routes
"""
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from app.core.database import get_db
from app.services.user_service import UserService
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListResponse
from app.schemas.category import CategoryCreate
from app.schemas.location import LocationCreate
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["users"])


class EmptyResponse(BaseModel):
    """Empty response model"""
    pass


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
                {"id": loc.id, "name": loc.name, "description": loc.description, "photo_url": loc.photo_url}
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
    user_id: int,
    category_data: CategoryCreate,
    db: Session = Depends(get_db)
) -> dict:
    """
    Create a new category for a specific user
    
    - **user_id**: The user's ID
    - **category_data**: Category information (code, name, description, classification)
    
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
