"""
Storage client for local filesystem and cloud storage (Supabase)
"""
import os
import uuid
import logging
from typing import Optional
from io import BytesIO
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)

class StorageException(Exception):
    """Storage operation exception"""
    pass


class StorageClient:
    """Storage client with support for local filesystem and Supabase Storage"""
    
    _supabase_client: Optional[Client] = None

    @classmethod
    def get_supabase_client(cls) -> Client:
        """Initialize and return Supabase client"""
        if not cls._supabase_client:
            if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
                raise StorageException("Supabase URL and Key must be configured for cloud storage")
            cls._supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        return cls._supabase_client

    @classmethod
    def upload_image(cls, file_content: BytesIO, filename: str, folder: str) -> str:
        """
        Upload image to storage (Supabase in prod, local in dev)
        
        Args:
            file_content: File content (BytesIO)
            filename: Original filename
            folder: Folder path (e.g., "documents/user_1")
            
        Returns:
            Storage URL
        """
        try:
            # Generate unique filename
            file_ext = os.path.splitext(filename)[1]
            unique_filename = f"{uuid.uuid4()}{file_ext}"
            full_path = f"{folder}/{unique_filename}"

            if settings.APP_ENV == "prod":
                return cls._upload_to_supabase(file_content, full_path)
            else:
                return cls._upload_to_local(file_content, full_path)
                
        except Exception as e:
            logger.error(f"Failed to upload image: {str(e)}")
            raise StorageException(f"Failed to upload image: {str(e)}")

    @classmethod
    def _upload_to_supabase(cls, file_content: BytesIO, full_path: str) -> str:
        """Upload to Supabase Storage"""
        client = cls.get_supabase_client()
        bucket = settings.SUPABASE_BUCKET
        
        # Ensure we're at the beginning of the buffer
        file_content.seek(0)
        content_bytes = file_content.read()
        
        # Upload
        res = client.storage.from_(bucket).upload(
            path=full_path,
            file=content_bytes,
            file_options={"content-type": "image/jpeg"} # Most common, can be improved
        )
        
        # Return the public URL
        # res looks like {'path': '...'} or raises an error
        public_url = client.storage.from_(bucket).get_public_url(full_path)
        return public_url

    @classmethod
    def _upload_to_local(cls, file_content: BytesIO, full_path: str) -> str:
        """Upload to local filesystem"""
        storage_dir = settings.STORAGE_LOCAL_PATH
        os.makedirs(os.path.join(storage_dir, os.path.dirname(full_path)), exist_ok=True)
        
        local_file_path = os.path.join(storage_dir, full_path)
        
        with open(local_file_path, 'wb') as f:
            f.write(file_content.getvalue())
        
        # Return absolute file path
        return os.path.abspath(local_file_path)
    
    @classmethod
    def delete_image(cls, image_url: str) -> bool:
        """
        Delete image from storage
        
        Args:
            image_url: Storage URL or path
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            if settings.APP_ENV == "prod":
                return cls._delete_from_supabase(image_url)
            else:
                return cls._delete_from_local(image_url)
        except Exception as e:
            logger.error(f"Failed to delete image: {str(e)}")
            return False

    @classmethod
    def _delete_from_supabase(cls, image_url: str) -> bool:
        """Delete from Supabase Storage"""
        client = cls.get_supabase_client()
        bucket = settings.SUPABASE_BUCKET
        
        # Extract path from URL (simple version)
        # Public URL format: https://[id].supabase.co/storage/v1/object/public/[bucket]/[path]
        search_str = f"/public/{bucket}/"
        if search_str in image_url:
            path = image_url.split(search_str)[1]
            client.storage.from_(bucket).remove([path])
            return True
        return False

    @classmethod
    def _delete_from_local(cls, image_url: str) -> bool:
        """Delete from local filesystem"""
        if image_url.startswith("file://"):
            local_path = image_url.replace("file://", "")
        else:
            local_path = image_url
        
        if os.path.exists(local_path):
            os.remove(local_path)
            return True
        return False
