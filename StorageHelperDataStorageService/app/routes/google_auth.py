"""
Google OAuth authentication routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.google_auth_service import GoogleAuthService
from app.schemas.user import GoogleTokenRequest, GoogleAuthResponse

router = APIRouter(prefix="/auth/google", tags=["authentication"])


@router.post(
    "/login",
    response_model=GoogleAuthResponse,
    summary="Google OAuth Login",
    description="Authenticate user with Google ID token. Creates new user if not exists, returns existing user ID if already registered."
)
def google_login(
    request: GoogleTokenRequest,
    db: Session = Depends(get_db)
):
    """
    Authenticate user with Google OAuth token.
    
    - **token**: Google ID token from client-side authentication
    
    Returns:
    - **user_id**: System user ID (auto-assigned if new user)
    - **is_new_user**: Whether this is a newly created user
    - **email**: User email from Google account
    - **display_name**: User display name
    
    Raises:
    - 400: Invalid token or token verification failed
    - 500: Server error
    """
    try:
        # Authenticate user with Google token
        auth_result = GoogleAuthService.authenticate_user(db, request.token)
        
        # Generate auth token (simplified format: "user_<id>")
        # In production, this should be a proper JWT token
        auth_token = f"user_{auth_result['user_id']}"
        
        return GoogleAuthResponse(
            user_id=auth_result["user_id"],
            is_new_user=auth_result["is_new_user"],
            email=auth_result["email"],
            display_name=auth_result["display_name"],
            auth_token=auth_token
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication failed: {str(e)}"
        )
