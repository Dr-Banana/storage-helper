"""
Storage client for local filesystem and cloud storage (Supabase)
"""
import os
import uuid
import logging
import shutil
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
            
            # CRITICAL FIX: Aggressively remove all proxy environment variables.
            # Newer versions of httpx (0.28+) can cause Client.__init__ proxy argument errors
            # with certain supabase-py components if these variables are present.
            proxy_vars = {}
            proxy_keys = [
                'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy',
                'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE'
            ]
            
            for key in proxy_keys:
                if key in os.environ:
                    proxy_vars[key] = os.environ.pop(key)
            
            try:
                # Import here to ensure environment variables are gone before library initialization
                from supabase import create_client
                cls._supabase_client = create_client(
                    settings.SUPABASE_URL, 
                    settings.SUPABASE_KEY
                )
                logger.info("Successfully initialized Supabase client (with proxy bypass)")
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {str(e)}")
                raise StorageException(f"Supabase client initialization failed: {str(e)}")
            finally:
                # Restore environment variables
                for key, value in proxy_vars.items():
                    os.environ[key] = value
                    
        return cls._supabase_client

    @classmethod
    def upload_image(cls, file_content: BytesIO, filename: str, folder: str, is_temporary: bool = False) -> str:
        """
        Upload image to storage (Supabase in prod/preprod, local in dev)
        
        Args:
            file_content: File content (BytesIO)
            filename: Original filename
            folder: Folder path (e.g., "documents/user_1")
            is_temporary: If True, upload to tmp/ folder for preview. These files can be deleted later if not confirmed.
            
        Returns:
            Storage URL
        """
        try:
            # Generate unique filename
            file_ext = os.path.splitext(filename)[1]
            unique_filename = f"{uuid.uuid4()}{file_ext}"
            
            # If temporary, add tmp/ prefix
            if is_temporary:
                full_path = f"tmp/{folder}/{unique_filename}"
                logger.info(f"Uploading as temporary file: {full_path}")
            else:
                full_path = f"{folder}/{unique_filename}"

            # Log current environment and storage decision
            logger.info(f"Storage decision: APP_ENV={settings.APP_ENV}, SUPABASE_URL={'configured' if settings.SUPABASE_URL else 'not configured'}, is_temporary={is_temporary}")
            
            # Use Supabase for both prod and preprod environments
            if settings.APP_ENV in ("prod", "preprod"):
                if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
                    logger.warning(f"APP_ENV is '{settings.APP_ENV}' but Supabase is not configured. Falling back to local storage.")
                    logger.warning(f"SUPABASE_URL: {settings.SUPABASE_URL}, SUPABASE_KEY: {'configured' if settings.SUPABASE_KEY else 'not configured'}")
                    return cls._upload_to_local(file_content, full_path)
                logger.info(f"Using Supabase storage for APP_ENV={settings.APP_ENV}")
                return cls._upload_to_supabase(file_content, full_path)
            else:
                logger.info(f"Using local storage for APP_ENV={settings.APP_ENV}")
                return cls._upload_to_local(file_content, full_path)
                
        except Exception as e:
            logger.error(f"Failed to upload image: {str(e)}")
            raise StorageException(f"Failed to upload image: {str(e)}")

    @classmethod
    def _upload_to_supabase(cls, file_content: BytesIO, full_path: str) -> str:
        """Upload to Supabase Storage"""
        logger.info(f"Uploading to Supabase: bucket={settings.SUPABASE_BUCKET}, path={full_path}")
        client = cls.get_supabase_client()
        bucket = settings.SUPABASE_BUCKET
        
        # Ensure we're at the beginning of the buffer
        file_content.seek(0)
        content_bytes = file_content.read()
        logger.debug(f"File size: {len(content_bytes)} bytes")
        
        # Upload
        res = client.storage.from_(bucket).upload(
            path=full_path,
            file=content_bytes,
            file_options={"content-type": "image/jpeg"} # Most common, can be improved
        )
        
        # Return the public URL
        # res looks like {'path': '...'} or raises an error
        public_url = client.storage.from_(bucket).get_public_url(full_path)
        logger.info(f"Successfully uploaded to Supabase. Public URL: {public_url}")
        return public_url

    @classmethod
    def _upload_to_local(cls, file_content: BytesIO, full_path: str) -> str:
        """Upload to local filesystem"""
        logger.info(f"Uploading to local storage: path={full_path}")
        storage_dir = settings.STORAGE_LOCAL_PATH
        
        # Normalize path separators for the current OS (convert / to \ on Windows)
        normalized_path = full_path.replace("/", os.sep)
        
        # Create directory structure
        dir_path = os.path.dirname(normalized_path)
        if dir_path:
            os.makedirs(os.path.join(storage_dir, dir_path), exist_ok=True)
        
        # Create full file path
        local_file_path = os.path.join(storage_dir, normalized_path)
        
        with open(local_file_path, 'wb') as f:
            f.write(file_content.getvalue())
        
        # Return absolute file path
        abs_path = os.path.abspath(local_file_path)
        logger.info(f"Successfully uploaded to local storage. Path: {abs_path}")
        return abs_path
    
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
            # Use Supabase for both prod and preprod environments
            if settings.APP_ENV in ("prod", "preprod"):
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
    
    @classmethod
    def move_from_temp(cls, temp_image_url: str) -> str:
        """
        Move image from tmp/ folder to permanent storage
        
        Args:
            temp_image_url: Storage URL of temporary image (should have tmp/ prefix)
            
        Returns:
            New storage URL of permanent image
        """
        try:
            # Use Supabase for both prod and preprod environments
            if settings.APP_ENV in ("prod", "preprod"):
                return cls._move_from_temp_supabase(temp_image_url)
            else:
                return cls._move_from_temp_local(temp_image_url)
        except Exception as e:
            logger.error(f"Failed to move image from temp: {str(e)}")
            raise StorageException(f"Failed to move image from temp: {str(e)}")
    
    @classmethod
    def _move_from_temp_supabase(cls, temp_image_url: str) -> str:
        """Move from tmp/ to permanent storage in Supabase"""
        client = cls.get_supabase_client()
        bucket = settings.SUPABASE_BUCKET
        
        # Extract path from URL
        search_str = f"/public/{bucket}/"
        if search_str not in temp_image_url:
            logger.error(f"Invalid Supabase URL format: {temp_image_url}")
            raise StorageException("Invalid Supabase URL format")
        
        temp_path = temp_image_url.split(search_str)[1]
        
        # Check if it's a tmp path
        if not temp_path.startswith("tmp/"):
            logger.warning(f"Image is not in tmp folder, returning as-is: {temp_path}")
            return temp_image_url
        
        # Generate permanent path (remove tmp/ prefix)
        permanent_path = temp_path.replace("tmp/", "", 1)
        
        logger.info(f"Moving file from {temp_path} to {permanent_path}")
        
        try:
            # Download from temp path
            download_response = client.storage.from_(bucket).download(temp_path)
            
            # Upload to permanent path
            client.storage.from_(bucket).upload(
                path=permanent_path,
                file=download_response,
                file_options={"content-type": "image/jpeg"}
            )
            
            # Delete temp file
            client.storage.from_(bucket).remove([temp_path])
            
            # Return permanent URL
            permanent_url = client.storage.from_(bucket).get_public_url(permanent_path)
            logger.info(f"Successfully moved to permanent storage: {permanent_url}")
            return permanent_url
            
        except Exception as e:
            logger.error(f"Failed to move file in Supabase: {str(e)}")
            raise StorageException(f"Failed to move file in Supabase: {str(e)}")
    
    @classmethod
    def _move_from_temp_local(cls, temp_image_url: str) -> str:
        """Move from tmp/ to permanent storage in local filesystem"""
        # Handle both file:// URLs and absolute paths
        if temp_image_url.startswith("file://"):
            temp_path = temp_image_url.replace("file://", "")
        else:
            temp_path = temp_image_url
        
        # Check if file exists
        if not os.path.exists(temp_path):
            logger.error(f"Temp file does not exist: {temp_path}")
            raise StorageException("Temp file does not exist")
        
        # Check if it's in tmp folder
        storage_dir = settings.STORAGE_LOCAL_PATH
        relative_path = os.path.relpath(temp_path, storage_dir)
        
        # Normalize path separators to forward slashes for consistent checking
        normalized_relative_path = relative_path.replace(os.sep, "/")
        
        # Check for tmp/ prefix (with trailing slash to avoid matching tmpfiles/, tmp-archive/, etc.)
        if not normalized_relative_path.startswith("tmp/"):
            logger.warning(f"Image is not in tmp folder, returning as-is: {relative_path}")
            return temp_image_url
        
        # Generate permanent path (remove tmp/ prefix) - works on both Windows and Unix
        permanent_relative_path = normalized_relative_path.replace("tmp/", "", 1)
        # Convert back to OS-specific path separators
        permanent_relative_path = permanent_relative_path.replace("/", os.sep)
        permanent_path = os.path.join(storage_dir, permanent_relative_path)
        
        logger.info(f"Moving file from {temp_path} to {permanent_path}")
        
        try:
            # Create permanent directory if needed
            os.makedirs(os.path.dirname(permanent_path), exist_ok=True)
            
            # Move file
            shutil.move(temp_path, permanent_path)
            
            # Return permanent path
            logger.info(f"Successfully moved to permanent storage: {permanent_path}")
            return os.path.abspath(permanent_path)
            
        except Exception as e:
            logger.error(f"Failed to move file in local storage: {str(e)}")
            raise StorageException(f"Failed to move file in local storage: {str(e)}")
    
    @classmethod
    def cleanup_old_temp_files(cls, days: int = 7) -> int:
        """
        Clean up temporary files older than specified days
        
        Args:
            days: Delete files older than this many days (default: 7)
            
        Returns:
            Number of files deleted
        """
        try:
            if settings.APP_ENV in ("prod", "preprod"):
                return cls._cleanup_old_temp_files_supabase(days)
            else:
                return cls._cleanup_old_temp_files_local(days)
        except Exception as e:
            logger.error(f"Failed to cleanup old temp files: {str(e)}")
            return 0
    
    @classmethod
    def _cleanup_old_temp_files_supabase(cls, days: int) -> int:
        """Clean up old temp files in Supabase"""
        from datetime import datetime, timedelta, timezone
        
        client = cls.get_supabase_client()
        bucket = settings.SUPABASE_BUCKET
        
        def list_files_recursively(prefix: str) -> list:
            """Recursively list all files in a folder"""
            all_files = []
            try:
                items = client.storage.from_(bucket).list(prefix, {
                    "limit": 1000,
                    "offset": 0,
                    "sortBy": {"column": "name", "order": "asc"}
                })
                
                for item in items:
                    item_name = item.get("name")
                    item_id = item.get("id")
                    
                    # Check if it's a folder (has 'id' as null or is a folder type)
                    # Supabase folders typically don't have 'id' field or have specific metadata
                    metadata = item.get("metadata")
                    
                    # If item has no specific file metadata, it's likely a folder - recurse into it
                    if metadata is None or item_id is None:
                        # It's a folder, recurse
                        subfolder_path = f"{prefix}/{item_name}" if prefix else item_name
                        all_files.extend(list_files_recursively(subfolder_path))
                    else:
                        # It's a file, add with full path
                        file_path = f"{prefix}/{item_name}" if prefix else item_name
                        all_files.append({
                            "path": file_path,
                            "created_at": item.get("created_at"),
                            "metadata": metadata
                        })
                        
            except Exception as e:
                logger.warning(f"Failed to list files in {prefix}: {e}")
            
            return all_files
        
        try:
            # Recursively list all files in tmp/ folder
            logger.info("Scanning tmp/ folder recursively for old files...")
            all_temp_files = list_files_recursively("tmp")
            logger.info(f"Found {len(all_temp_files)} total files in tmp/ folder")
            
            # Use timezone-aware datetime for comparison (Supabase timestamps are in UTC)
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
            deleted_count = 0
            
            for file_info in all_temp_files:
                file_path = file_info.get("path")
                created_at_str = file_info.get("created_at")
                
                if not created_at_str or not file_path:
                    continue
                
                # Parse created_at timestamp (Supabase returns UTC timestamps)
                try:
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    
                    # Check if file is old enough to delete
                    if created_at < cutoff_time:
                        logger.info(f"Deleting old temp file: {file_path} (created: {created_at})")
                        client.storage.from_(bucket).remove([file_path])
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to process file {file_path}: {e}")
                    continue
            
            logger.info(f"Cleanup complete. Deleted {deleted_count} old temp files from Supabase")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup old temp files in Supabase: {str(e)}")
            return 0
    
    @classmethod
    def _cleanup_old_temp_files_local(cls, days: int) -> int:
        """Clean up old temp files in local filesystem"""
        import time
        from datetime import datetime, timedelta
        
        storage_dir = settings.STORAGE_LOCAL_PATH
        tmp_dir = os.path.join(storage_dir, "tmp")
        
        if not os.path.exists(tmp_dir):
            logger.info("No tmp directory found, nothing to clean up")
            return 0
        
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        deleted_count = 0
        
        try:
            # Walk through tmp directory
            for root, dirs, files in os.walk(tmp_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    
                    try:
                        # Check file modification time
                        file_mtime = os.path.getmtime(file_path)
                        
                        if file_mtime < cutoff_time:
                            logger.info(f"Deleting old temp file: {file_path}")
                            os.remove(file_path)
                            deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to process file {file_path}: {e}")
                        continue
            
            # Remove empty directories
            for root, dirs, files in os.walk(tmp_dir, topdown=False):
                for dirname in dirs:
                    dir_path = os.path.join(root, dirname)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                            logger.info(f"Removed empty directory: {dir_path}")
                    except Exception as e:
                        logger.warning(f"Failed to remove directory {dir_path}: {e}")
            
            logger.info(f"Cleanup complete. Deleted {deleted_count} old temp files from local storage")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup old temp files in local storage: {str(e)}")
            return 0
