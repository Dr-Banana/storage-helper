"""
Database models for User
"""
from datetime import datetime
from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime, JSON, func, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base

COOKING_LEVEL_BEGINNER = "beginner"
COOKING_LEVEL_INTERMEDIATE = "intermediate"
COOKING_LEVEL_EXPERT = "expert"
COOKING_LEVEL_VALUES = [COOKING_LEVEL_BEGINNER, COOKING_LEVEL_INTERMEDIATE, COOKING_LEVEL_EXPERT]

USER_LANGUAGE_VALUES = ["zh", "en", "ja", "ko"]


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
    cooking_level = Column(String(20), nullable=False, server_default=COOKING_LEVEL_BEGINNER)
    language = Column(String(10), nullable=False, server_default="zh")
    # Dining preferences — hard constraints fed to the LLM meal planner
    default_servings = Column(Integer, nullable=False, server_default="1")
    meat_veg_ratio = Column(String(20), nullable=False, server_default="1:1:1")
    include_soup = Column(Boolean, nullable=False, server_default="true")
    calorie_target = Column(Integer, nullable=True)
    # Meal diversity & personalization fields
    disliked_ingredients = Column(JSON, nullable=True, server_default="[]")
    cuisine_weights = Column(JSON, nullable=True, server_default='{"Chinese": 50, "Western": 20, "Japanese": 15, "Korean": 10, "Other": 5}')
    recent_dishes = Column(JSON, nullable=True, server_default="[]")
    # Subscription fields
    is_premium = Column(Boolean, nullable=False, server_default="false")
    premium_expiry = Column(DateTime, nullable=True)   # None = no expiry (lifetime), set = expires at
    premium_source = Column(String(50), nullable=True)  # "manual" | "google_play" | "stripe"
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    schedules = relationship("Schedule", back_populates="user")
    daily_usages = relationship("DailyUsage", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, google_id='{self.google_id}', display_name='{self.display_name}')>"
