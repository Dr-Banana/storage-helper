"""
Database model for DocumentEmbedding
"""
from sqlalchemy import Column, Integer, ForeignKey, String, TypeDecorator, text
from sqlalchemy.sql import func
import json

from app.core.database import Base


class MySQLVectorType(TypeDecorator):
    """
    Custom type for MySQL VECTOR type.
    MySQL VECTOR type requires STRING_TO_VECTOR('[0.1, 0.2, ...]') format.
    """
    impl = String
    cache_ok = True
    
    def __init__(self, dimension, *args, **kwargs):
        self.dimension = dimension
        super().__init__(*args, **kwargs)
    
    def process_bind_param(self, value, dialect):
        """Convert Python list to JSON string for MySQL STRING_TO_VECTOR function"""
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError(f"Expected list, got {type(value)}")
        if len(value) != self.dimension:
            raise ValueError(f"Expected list of length {self.dimension}, got {len(value)}")
        # Convert list to JSON string format that STRING_TO_VECTOR accepts
        return json.dumps(value)
    
    def bind_expression(self, bindvalue):
        """Use STRING_TO_VECTOR SQL function to convert JSON string to VECTOR"""
        from sqlalchemy import func
        # bindvalue is already a bind parameter (the JSON string from process_bind_param)
        # Wrap it with STRING_TO_VECTOR SQL function
        # Use func to create a generic function call since STRING_TO_VECTOR is MySQL-specific
        return func.STRING_TO_VECTOR(bindvalue)
    
    def process_result_value(self, value, dialect):
        """Convert from database format back to Python list"""
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        if isinstance(value, list):
            return value
        return value
    
    def load_dialect_impl(self, dialect):
        """Use String as base type but handle conversion in SQL"""
        return dialect.type_descriptor(String)


class DocumentEmbedding(Base):
    """DocumentEmbedding model - semantic vector representation for semantic search"""
    __tablename__ = "document_embedding"

    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"), primary_key=True)
    
    # Use custom MySQLVectorType for MySQL VECTOR(768) type
    # This handles the conversion between Python list and MySQL VECTOR format
    embedding = Column(MySQLVectorType(768), nullable=False)  # 768-dimensional vector for semantic search

    def __repr__(self):
        return f"<DocumentEmbedding(document_id={self.document_id})>"
