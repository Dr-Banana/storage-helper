"""
Storage client for local filesystem storage
"""
import os
import uuid
from typing import Optional
from io import BytesIO


class StorageException(Exception):
    """Storage operation exception"""
    pass


class StorageClient:
    """Local filesystem storage client"""
    
    @staticmethod
    def upload_image(file_content: BytesIO, filename: str, folder: str) -> str:
        """
        Upload image to local filesystem
        
        Args:
            file_content: File content (BytesIO)
            filename: Original filename
            folder: Folder path (e.g., "documents/user_1")
            
        Returns:
            Storage URL (file:// path)
        """
        try:
            # Generate unique filename
            file_ext = os.path.splitext(filename)[1]
            unique_filename = f"{uuid.uuid4()}{file_ext}"
            full_path = f"{folder}/{unique_filename}"
            
            storage_dir = os.getenv("STORAGE_LOCAL_PATH", "/tmp/storage")
            os.makedirs(os.path.join(storage_dir, os.path.dirname(full_path)), exist_ok=True)
            
            local_file_path = os.path.join(storage_dir, full_path)
            
            with open(local_file_path, 'wb') as f:
                f.write(file_content.getvalue())
            
            # Return file path
            return f"file://{local_file_path}"
            
        except Exception as e:
            raise StorageException(f"Failed to upload image: {str(e)}")
    
    @staticmethod
    def delete_image(image_url: str) -> bool:
        """
        Delete image from local filesystem
        
        Args:
            image_url: File URL (file:// path)
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            if image_url.startswith("file://"):
                local_path = image_url.replace("file://", "")
                if os.path.exists(local_path):
                    os.remove(local_path)
                    return True
            return False
        except Exception as e:
            raise StorageException(f"Failed to delete image: {str(e)}")
