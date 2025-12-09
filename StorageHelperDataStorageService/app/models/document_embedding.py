"""
Database model for DocumentEmbedding
"""
from sqlalchemy import Column, Integer, ForeignKey, JSON

from app.core.database import Base

try:
    # MySQL 8.0.32+ and MySQL 9.x support VECTOR type
    from sqlalchemy.dialects.mysql import VECTOR
except ImportError:
    VECTOR = None


class DocumentEmbedding(Base):
    """DocumentEmbedding model - semantic vector representation for semantic search"""
    __tablename__ = "document_embedding"

    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"), primary_key=True)
    
    # Use VECTOR(768) for MySQL 8.0.32+ and 9.x, fallback to JSON for older versions
    if VECTOR is not None:
        embedding = Column(VECTOR(768), nullable=False)  # 768-dimensional vector for semantic search
    else:
        embedding = Column(JSON, nullable=False)  # Fallback: [0.123, -0.98, 0.456, ...]

    def __repr__(self):
        return f"<DocumentEmbedding(document_id={self.document_id})>"
