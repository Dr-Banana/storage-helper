"""
Database model for StorageLocation
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text

from app.core.database import Base


class StorageLocation(Base):
    """StorageLocation model - physical storage locations (cabinet, drawer, box, etc.)"""
    __tablename__ = "storage_location"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)  # e.g. "Bedroom desk, left drawer #2"
    description = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)

    def __repr__(self):
        return f"<StorageLocation(id={self.id}, name='{self.name}')>"
