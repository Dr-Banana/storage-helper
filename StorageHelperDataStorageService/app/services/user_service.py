"""
User business logic service
"""
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


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
                display_name=user_data.display_name,
                note=user_data.note
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
