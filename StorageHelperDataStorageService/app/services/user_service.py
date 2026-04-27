"""
User business logic service
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.models.document import Document
from app.models.document_page import DocumentPage
from app.models.storage_location import StorageLocation
from app.schemas.user import UserCreate, UserUpdate
from app.integrations.storage_client import StorageClient

logger = logging.getLogger(__name__)


class UserService:
    """Service for user-related business logic"""
    
    @staticmethod
    def create_user(db: Session, user_data: UserCreate) -> User:
        """
        Create a new user
        
        Args:
            db: Database session
            user_data: User creation data
            
        Returns:
            Created user object
            
        Raises:
            ValueError: If user creation fails
        """
        try:
            new_user = User(
                google_id=user_data.google_id,
                email=user_data.email,
                display_name=user_data.display_name,
                note=user_data.note,
                cooking_level=user_data.cooking_level if hasattr(user_data, "cooking_level") and user_data.cooking_level else "beginner",
                language=user_data.language if hasattr(user_data, "language") and user_data.language else "zh",
            )
            db.add(new_user)
            db.commit()
            db.refresh(new_user)
            return new_user
        except IntegrityError as e:
            db.rollback()
            raise ValueError(f"User creation failed: {str(e)}")
        except Exception as e:
            db.rollback()
            raise ValueError(f"Unexpected error during user creation: {str(e)}")
    
    @staticmethod
    def get_all_users(db: Session) -> list[User]:
        """
        Get all users
        
        Args:
            db: Database session
            
        Returns:
            List of all users
        """
        return db.query(User).all()
    
    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> User:
        """
        Get a user by ID
        
        Args:
            db: Database session
            user_id: User ID
            
        Returns:
            User object if found, None otherwise
        """
        return db.query(User).filter(User.id == user_id).first()
    
    @staticmethod
    def update_user(db: Session, user_id: int, user_data: UserUpdate) -> User:
        """
        Update a user
        
        Args:
            db: Database session
            user_id: User ID
            user_data: Update data (fields to update are optional)
            
        Returns:
            Updated user object
            
        Raises:
            ValueError: If user not found or update fails
        """
        try:
            user = UserService.get_user_by_id(db, user_id)
            
            if not user:
                raise ValueError(f"User with ID {user_id} not found")
            
            # Update only provided fields
            if user_data.display_name is not None:
                user.display_name = user_data.display_name
            if user_data.note is not None:
                user.note = user_data.note
            if user_data.cooking_level is not None:
                user.cooking_level = user_data.cooking_level
            if user_data.language is not None:
                user.language = user_data.language
            if user_data.default_servings is not None:
                user.default_servings = user_data.default_servings
            if user_data.meat_veg_ratio is not None:
                user.meat_veg_ratio = user_data.meat_veg_ratio
            if user_data.include_soup is not None:
                user.include_soup = user_data.include_soup
            # calorie_target can be explicitly set to None to clear it
            if hasattr(user_data, 'calorie_target') and 'calorie_target' in user_data.model_fields_set:
                user.calorie_target = user_data.calorie_target
            if user_data.disliked_ingredients is not None:
                user.disliked_ingredients = user_data.disliked_ingredients
            if user_data.cuisine_weights is not None:
                user.cuisine_weights = user_data.cuisine_weights
            if user_data.recent_dishes is not None:
                user.recent_dishes = user_data.recent_dishes

            db.commit()
            db.refresh(user)
            return user
        except ValueError:
            raise
        except Exception as e:
            db.rollback()
            raise ValueError(f"Failed to update user: {str(e)}")
    
    @staticmethod
    def delete_user(db: Session, user_id: int) -> bool:
        """
        Delete a user
        
        Args:
            db: Database session
            user_id: User ID
            
        Returns:
            True if deletion successful
            
        Raises:
            ValueError: If user not found or deletion fails
        """
        try:
            user = UserService.get_user_by_id(db, user_id)
            
            if not user:
                raise ValueError(f"User with ID {user_id} not found")
            
            db.delete(user)
            db.commit()
            return True
        except ValueError:
            raise
        except Exception as e:
            db.rollback()
            raise ValueError(f"Failed to delete user: {str(e)}")
    
    @staticmethod
    def get_subscription_status(db: Session, user_id: int) -> dict:
        """Return subscription status for a user, handling expiry check."""
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise ValueError(f"User with ID {user_id} not found")

        now = datetime.now(timezone.utc)
        is_active = bool(
            user.is_premium and (
                user.premium_expiry is None
                or user.premium_expiry.replace(tzinfo=timezone.utc) > now
            )
        )
        return {
            "user_id": user_id,
            "is_premium": user.is_premium,
            "premium_expiry": user.premium_expiry.isoformat() if user.premium_expiry else None,
            "premium_source": user.premium_source,
            "is_active": is_active,
        }

    @staticmethod
    def set_subscription(
        db: Session,
        user_id: int,
        is_premium: bool,
        premium_expiry: Optional[datetime] = None,
        premium_source: Optional[str] = "manual",
    ) -> dict:
        """Set subscription state for a user (admin/manual use)."""
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            raise ValueError(f"User with ID {user_id} not found")
        try:
            user.is_premium = is_premium
            user.premium_expiry = premium_expiry
            user.premium_source = premium_source if is_premium else None
            db.commit()
            db.refresh(user)
            return UserService.get_subscription_status(db, user_id)
        except Exception as e:
            db.rollback()
            raise ValueError(f"Failed to update subscription: {str(e)}")

    @staticmethod
    def erase_all_user_data(db: Session, user_id: int) -> dict:
        """
        Erase all data for a user, including:
        - All documents and their files from storage
        - All storage locations and their images
        - All document categories
        - All schedules
        - Finally, the user account itself
        
        Args:
            db: Database session
            user_id: User ID
            
        Returns:
            Dictionary with deletion statistics
            
        Raises:
            ValueError: If user not found or deletion fails
        """
        try:
            user = UserService.get_user_by_id(db, user_id)
            
            if not user:
                raise ValueError(f"User with ID {user_id} not found")
            
            stats = {
                "documents_deleted": 0,
                "files_deleted": 0,
                "locations_deleted": 0,
                "location_images_deleted": 0,
                "categories_deleted": 0,
                "schedules_deleted": 0
            }
            
            # 1. Delete all documents and their files from storage
            documents = db.query(Document).filter(Document.owner_id == user_id).all()
            stats["documents_deleted"] = len(documents)
            
            for document in documents:
                # Get all page image URLs
                pages = db.query(DocumentPage).filter(DocumentPage.document_id == document.id).all()
                image_urls = [page.image_url for page in pages if page.image_url]
                
                # Include document thumbnail if different
                if document.image_url and document.image_url not in image_urls:
                    image_urls.append(document.image_url)
                
                # Delete files from storage
                for url in image_urls:
                    try:
                        if StorageClient.delete_image(url):
                            stats["files_deleted"] += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete file from storage: {url}. Error: {e}")
            
            # 2. Delete all storage location images
            locations = db.query(StorageLocation).filter(StorageLocation.user_id == user_id).all()
            stats["locations_deleted"] = len(locations)
            
            for location in locations:
                if location.photo_url:
                    try:
                        if StorageClient.delete_image(location.photo_url):
                            stats["location_images_deleted"] += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete location image: {location.photo_url}. Error: {e}")
            
            # 3. Count categories and schedules (will be cascade deleted)
            from app.models.document_category import DocumentCategory
            from app.models.schedule import Schedule
            
            categories = db.query(DocumentCategory).filter(DocumentCategory.user_id == user_id).all()
            stats["categories_deleted"] = len(categories)
            
            schedules = db.query(Schedule).filter(Schedule.user_id == user_id).all()
            stats["schedules_deleted"] = len(schedules)
            
            # 4. Delete the user (cascade will handle documents, locations, categories, schedules)
            db.delete(user)
            db.commit()
            
            logger.info(f"Successfully erased all data for user {user_id}. Stats: {stats}")
            return stats
            
        except ValueError:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to erase user data: {e}")
            raise ValueError(f"Failed to erase user data: {str(e)}")
