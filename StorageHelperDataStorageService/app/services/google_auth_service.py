"""
Google OAuth authentication service
"""
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.schemas.user import UserCreate


class GoogleAuthService:
    """Service for Google OAuth authentication"""
    
    # You should set this in environment variables in production
    # For now, this should be configured via environment
    GOOGLE_CLIENT_ID = None
    
    @staticmethod
    def set_client_id(client_id: str):
        """Set Google Client ID"""
        GoogleAuthService.GOOGLE_CLIENT_ID = client_id
    
    @staticmethod
    def verify_token(token: str) -> dict:
        """
        Verify Google ID token and extract user information
        
        Args:
            token: Google ID token from client
            
        Returns:
            Dictionary containing user info from token (sub, email, name, etc.)
            
        Raises:
            ValueError: If token is invalid or verification fails
        """
        try:
            if not GoogleAuthService.GOOGLE_CLIENT_ID:
                raise ValueError("Google Client ID not configured")
            
            # Verify the token
            idinfo = id_token.verify_oauth2_token(
                token, 
                requests.Request(), 
                GoogleAuthService.GOOGLE_CLIENT_ID
            )
            
            # Token is valid
            return idinfo
        except ValueError as e:
            raise ValueError(f"Invalid token: {str(e)}")
        except Exception as e:
            raise ValueError(f"Token verification failed: {str(e)}")
    
    @staticmethod
    def authenticate_user(db: Session, token: str) -> dict:
        """
        Authenticate user with Google token.
        If user doesn't exist, create new user with auto-assigned ID.
        If user exists, return existing user ID.
        
        Args:
            db: Database session
            token: Google ID token
            
        Returns:
            Dictionary with:
                - user_id: System user ID (newly assigned or existing)
                - is_new_user: Boolean indicating if user was just created
                - email: User email
                - display_name: User display name
                
        Raises:
            ValueError: If token verification fails or user creation fails
        """
        try:
            # Verify the token
            idinfo = GoogleAuthService.verify_token(token)
            
            google_id = idinfo.get('sub')
            email = idinfo.get('email')
            display_name = idinfo.get('name', email.split('@')[0])
            
            if not google_id or not email:
                raise ValueError("Token missing required fields (sub or email)")
            
            # Check if user already exists
            existing_user = db.query(User).filter(
                User.google_id == google_id
            ).first()
            
            if existing_user:
                # User exists, return existing user ID
                return {
                    "user_id": existing_user.id,
                    "is_new_user": False,
                    "email": existing_user.email,
                    "display_name": existing_user.display_name
                }
            
            # User doesn't exist, create new user
            try:
                new_user = User(
                    google_id=google_id,
                    email=email,
                    display_name=display_name,
                    note=None
                )
                db.add(new_user)
                db.commit()
                db.refresh(new_user)
                
                return {
                    "user_id": new_user.id,
                    "is_new_user": True,
                    "email": new_user.email,
                    "display_name": new_user.display_name
                }
            except IntegrityError as e:
                db.rollback()
                raise ValueError(f"User creation failed - possible duplicate email: {str(e)}")
            except Exception as e:
                db.rollback()
                raise ValueError(f"User creation failed: {str(e)}")
        
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Authentication failed: {str(e)}")
    
    @staticmethod
    def get_user_by_google_id(db: Session, google_id: str) -> User:
        """
        Get user by Google ID
        
        Args:
            db: Database session
            google_id: Google user ID
            
        Returns:
            User object if found, None otherwise
        """
        return db.query(User).filter(User.google_id == google_id).first()
