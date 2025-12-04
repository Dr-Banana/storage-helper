"""
Database model for Event
"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, func

from app.core.database import Base


class Event(Base):
    """Event model - contextual grouping for documents (e.g. "2024 Tax Filing", "Q2 Dental Visit")"""
    __tablename__ = "event"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)  # e.g. "2024 Tax Filing", "Q2 Dental Visit"
    category = Column(String(50), nullable=True)  # tag for organizing events (independent from document_category)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Event(id={self.id}, name='{self.name}', category='{self.category}')>"
