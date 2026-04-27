"""
Service for free-tier usage tracking and limit enforcement.

Free tier limits:
  - 2 meal plan sessions per day
  - 20,000 tokens per day

Premium users have no limits.
"""
import logging
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.daily_usage import DailyUsage
from app.models.user import User

logger = logging.getLogger(__name__)

FREE_SESSION_LIMIT = 2
FREE_TOKEN_LIMIT = 20_000


class UsageService:

    @staticmethod
    def _get_or_create_today(db: Session, user_id: int) -> DailyUsage:
        """Fetch today's DailyUsage row, creating it if absent."""
        today = date.today()
        row = (
            db.query(DailyUsage)
            .filter(DailyUsage.user_id == user_id, DailyUsage.usage_date == today)
            .first()
        )
        if row is None:
            row = DailyUsage(user_id=user_id, usage_date=today)
            db.add(row)
            db.flush()
        return row

    @staticmethod
    def get_today_usage(db: Session, user_id: int) -> dict:
        """Return today's usage counters for a user."""
        today = date.today()
        row = (
            db.query(DailyUsage)
            .filter(DailyUsage.user_id == user_id, DailyUsage.usage_date == today)
            .first()
        )
        sessions = row.meal_plan_sessions if row else 0
        tokens = row.token_count if row else 0
        return {
            "usage_date": today.isoformat(),
            "meal_plan_sessions": sessions,
            "token_count": tokens,
        }

    @staticmethod
    def check_limits(db: Session, user_id: int, is_premium_active: bool) -> dict:
        """
        Check whether the user is allowed to start a new meal plan session.
        Returns a dict with 'allowed', 'reason', and current counts.
        """
        usage = UsageService.get_today_usage(db, user_id)
        sessions_used = usage["meal_plan_sessions"]
        tokens_used = usage["token_count"]

        if is_premium_active:
            return {
                "allowed": True,
                "reason": None,
                "sessions_used": sessions_used,
                "sessions_limit": None,
                "tokens_used": tokens_used,
                "tokens_limit": None,
            }

        if sessions_used >= FREE_SESSION_LIMIT:
            return {
                "allowed": False,
                "reason": "daily_session_limit",
                "sessions_used": sessions_used,
                "sessions_limit": FREE_SESSION_LIMIT,
                "tokens_used": tokens_used,
                "tokens_limit": FREE_TOKEN_LIMIT,
            }

        if tokens_used >= FREE_TOKEN_LIMIT:
            return {
                "allowed": False,
                "reason": "daily_token_limit",
                "sessions_used": sessions_used,
                "sessions_limit": FREE_SESSION_LIMIT,
                "tokens_used": tokens_used,
                "tokens_limit": FREE_TOKEN_LIMIT,
            }

        return {
            "allowed": True,
            "reason": None,
            "sessions_used": sessions_used,
            "sessions_limit": FREE_SESSION_LIMIT,
            "tokens_used": tokens_used,
            "tokens_limit": FREE_TOKEN_LIMIT,
        }

    @staticmethod
    def increment_session(db: Session, user_id: int) -> dict:
        """Atomically increment the meal plan session counter for today."""
        row = UsageService._get_or_create_today(db, user_id)
        row.meal_plan_sessions += 1
        db.commit()
        db.refresh(row)
        return {
            "usage_date": row.usage_date.isoformat(),
            "meal_plan_sessions": row.meal_plan_sessions,
            "token_count": row.token_count,
        }

    @staticmethod
    def add_tokens(db: Session, user_id: int, count: int) -> dict:
        """Add token consumption to today's counter."""
        if count <= 0:
            return UsageService.get_today_usage(db, user_id)
        row = UsageService._get_or_create_today(db, user_id)
        row.token_count += count
        db.commit()
        db.refresh(row)
        return {
            "usage_date": row.usage_date.isoformat(),
            "meal_plan_sessions": row.meal_plan_sessions,
            "token_count": row.token_count,
        }
