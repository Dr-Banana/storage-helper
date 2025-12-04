"""
Database model for Document
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, func

from app.core.database import Base


class Document(Base):
    """Document model - core entity for storing document metadata and references"""
    __tablename__ = "document"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(255), nullable=True, index=True)
    category_id = Column(Integer, ForeignKey("document_category.id", ondelete="RESTRICT"), nullable=True)
    owner_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(Integer, ForeignKey("event.id", ondelete="SET NULL"), nullable=True)
    current_location_id = Column(Integer, ForeignKey("storage_location.id", ondelete="SET NULL"), nullable=True)
    
    # Flexible metadata (per-type fields live here)
    # e.g. {"tax_year": 2024, "issuer_name": "IRS", "expiry_date": "2026-01-01"}
    doc_metadata = Column("metadata", JSON, nullable=True)
    
    # File content
    image_url = Column(Text, nullable=False)  # URL or path to document image
    ocr_text = Column(Text, nullable=True)  # Extracted text from OCR
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Document(id={self.id}, title='{self.title}', owner_id={self.owner_id}, category_id={self.category_id})>"
