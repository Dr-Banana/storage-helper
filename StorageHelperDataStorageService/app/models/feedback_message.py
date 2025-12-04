"""
Database model for FeedbackMessage
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func

from app.core.database import Base


class FeedbackMessage(Base):
    """FeedbackMessage model - user feedback for system improvement"""
    __tablename__ = "feedback_message"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"), nullable=True)
    feedback_type = Column(String(50), nullable=True)  # e.g. "type_fix", "location_error", "metadata_fix"
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    def __repr__(self):
        return f"<FeedbackMessage(id={self.id}, document_id={self.document_id}, feedback_type='{self.feedback_type}')>"
