"""
Authentication module for StorageHelper Data Storage Service
Provides identity verification for API endpoints
"""
from fastapi import HTTPException, status, Header
from typing import Optional


async def get_current_user_id(
    authorization: Optional[str] = Header(None)
) -> int:
    """
    Extract and verify user ID from Authorization header.
    
    Expected format: "Bearer user_<user_id>"
    Example: "Bearer user_1"
    
    In production, this should validate JWT tokens instead.
    
    Args:
        authorization: Authorization header value
        
    Returns:
        int: Verified user ID
        
    Raises:
        HTTPException: If authorization header is missing or invalid
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )
    
    try:
        # Parse "Bearer user_<id>"
        parts = authorization.split()
        
        if len(parts) != 2:
            raise ValueError("Invalid authorization format")
        
        scheme, credentials = parts
        
        if scheme.lower() != "bearer":
            raise ValueError("Invalid authorization scheme")
        
        # Extract user ID from credentials
        # Format: "user_1", "user_2", etc.
        if not credentials.startswith("user_"):
            raise ValueError("Invalid credentials format")
        
        user_id = int(credentials.split("_")[1])
        
        if user_id <= 0:
            raise ValueError("Invalid user ID")
        
        return user_id
        
    except (ValueError, IndexError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authorization token: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization verification failed"
        )
