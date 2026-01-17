"""
Authentication utility routes - Token verification and validation
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get(
    "/verify",
    summary="Verify Authentication Token",
    description="Verify that the provided Bearer token is valid and return the associated user ID."
)
def verify_token(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    """
    Verify the authentication token from the Authorization header.
    
    Returns:
    - **user_id**: The verified user ID from the token
    
    Raises:
    - 401: Invalid or missing authentication token
    - 404: User not found in database
    """
    # Verify user still exists in database
    user = db.query(User).filter(User.id == current_user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return {
        "user_id": current_user_id,
        "email": user.email,
        "display_name": user.display_name
    }
