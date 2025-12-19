"""
Database model for DocumentEmbedding
"""
from sqlalchemy import Column, Integer, ForeignKey
from pgvector.sqlalchemy import Vector

from app.core.database import Base


class DocumentEmbedding(Base):
    """DocumentEmbedding model - semantic vector representation for semantic search"""
    __tablename__ = "document_embedding"

    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"), primary_key=True)
    
    # Use pgvector Vector type for PostgreSQL
    embedding = Column(Vector(768), nullable=False)  # 768-dimensional vector for semantic search

    def __repr__(self):
        return f"<DocumentEmbedding(document_id={self.document_id})>"
