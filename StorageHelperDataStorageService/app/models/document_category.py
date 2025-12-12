"""
Database model for DocumentCategory
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, func, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base


class DocumentCategory(Base):
    """DocumentCategory model - document classifications (TAX, VISA, MED, INS, etc.)"""
    __tablename__ = "document_category"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(50), nullable=False, index=True)  # e.g. "TAX", "VISA", "MED"
    name = Column(String(100), nullable=False)  # e.g. "Tax Documents", "Immigration Documents"
    description = Column(Text, nullable=True)
    classification = Column(Text, nullable=True)  # e.g. virtual/physical (placeholder)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Unique constraint: code must be unique per user
    __table_args__ = (
        UniqueConstraint('user_id', 'code', name='uq_user_category_code'),
    )

    def __repr__(self):
        return f"<DocumentCategory(id={self.id}, user_id={self.user_id}, code='{self.code}', name='{self.name}')>"
