"""
Database model for StorageLocation
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func

from app.core.database import Base


class StorageLocation(Base):
    """StorageLocation model - physical storage locations (cabinet, drawer, box, etc.)"""
    __tablename__ = "storage_location"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)  # e.g. "Bedroom desk, left drawer #2"
    description = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey("storage_location.id", ondelete="SET NULL"), nullable=True)  # For hierarchical locations
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<StorageLocation(id={self.id}, name='{self.name}', parent_id={self.parent_id})>"
