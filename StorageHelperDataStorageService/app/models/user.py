"""
Database models for User
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, func, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    """User model"""
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint('google_id', name='uq_user_google_id'),
        {"quote": True}
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    google_id = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    schedules = relationship("Schedule", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, google_id='{self.google_id}', display_name='{self.display_name}')>"
