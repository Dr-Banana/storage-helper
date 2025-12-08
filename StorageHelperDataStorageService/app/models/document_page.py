"""
Database model for DocumentPage
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base


class DocumentPage(Base):
    """DocumentPage model - represents individual pages within a document"""
    __tablename__ = "document_page"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    image_url = Column(Text, nullable=False)  # URL or path to page image
    ocr_text = Column(Text, nullable=True)  # Extracted text from OCR for this page
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationship to document
    document = relationship("Document", back_populates="pages")
    
    __table_args__ = (
        UniqueConstraint('document_id', 'page_number', name='uq_document_page_number'),
    )

    def __repr__(self):
        return f"<DocumentPage(id={self.id}, document_id={self.document_id}, page_number={self.page_number})>"

