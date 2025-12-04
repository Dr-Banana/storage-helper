"""
Database models for User
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.core.database import Base


class User(Base):
    """User model"""
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    display_name = Column(String(100), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<User(id={self.id}, display_name='{self.display_name}')>"
