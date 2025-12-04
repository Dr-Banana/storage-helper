"""
Database model for DocumentEmbedding
"""
from sqlalchemy import Column, Integer, ForeignKey, JSON

from app.core.database import Base


class DocumentEmbedding(Base):
    """DocumentEmbedding model - semantic vector representation for semantic search"""
    __tablename__ = "document_embedding"

    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(JSON, nullable=False)  # e.g. [0.123, -0.98, 0.456, ...]

    def __repr__(self):
        return f"<DocumentEmbedding(document_id={self.document_id})>"
