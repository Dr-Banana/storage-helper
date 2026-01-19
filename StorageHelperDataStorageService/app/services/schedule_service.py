"""
Schedule service layer for business logic
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime
from typing import List, Optional
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate


class ScheduleService:
    """Service for schedule operations"""

    @staticmethod
    def create_schedule(db: Session, user_id: int, schedule_data: ScheduleCreate) -> Schedule:
        """Create a new schedule"""
        schedule = Schedule(
            user_id=user_id,
            title=schedule_data.title,
            event_type=schedule_data.event_type,
            description=schedule_data.description,
            scheduled_time=schedule_data.scheduled_time,
            end_time=schedule_data.end_time,
            location=schedule_data.location,
            priority=schedule_data.priority,
            extra_data=schedule_data.metadata,
        )
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        return schedule

    @staticmethod
    def get_schedule(db: Session, schedule_id: int, user_id: int) -> Optional[Schedule]:
        """Get a specific schedule by ID"""
        return db.query(Schedule).filter(
            and_(Schedule.id == schedule_id, Schedule.user_id == user_id)
        ).first()

    @staticmethod
    def get_user_schedules(db: Session, user_id: int) -> List[Schedule]:
        """Get all schedules for a user"""
        return db.query(Schedule).filter(Schedule.user_id == user_id).all()

    @staticmethod
    def get_user_schedules_by_range(
        db: Session,
        user_id: int,
        start_time: datetime,
        end_time: datetime
    ) -> List[Schedule]:
        """Get schedules within a date range for a user"""
        return db.query(Schedule).filter(
            and_(
                Schedule.user_id == user_id,
                or_(
                    and_(Schedule.scheduled_time >= start_time, Schedule.scheduled_time <= end_time),
                    and_(Schedule.end_time >= start_time, Schedule.end_time <= end_time),
                    and_(Schedule.scheduled_time <= start_time, Schedule.end_time >= end_time),
                )
            )
        ).order_by(Schedule.scheduled_time).all()

    @staticmethod
    def update_schedule(
        db: Session,
        schedule_id: int,
        user_id: int,
        schedule_data: ScheduleUpdate
    ) -> Optional[Schedule]:
        """Update a schedule"""
        schedule = db.query(Schedule).filter(
            and_(Schedule.id == schedule_id, Schedule.user_id == user_id)
        ).first()

        if not schedule:
            return None

        # Update only provided fields
        if schedule_data.title is not None:
            schedule.title = schedule_data.title
        if schedule_data.event_type is not None:
            schedule.event_type = schedule_data.event_type
        if schedule_data.description is not None:
            schedule.description = schedule_data.description
        if schedule_data.scheduled_time is not None:
            schedule.scheduled_time = schedule_data.scheduled_time
        if schedule_data.end_time is not None:
            schedule.end_time = schedule_data.end_time
        if schedule_data.location is not None:
            schedule.location = schedule_data.location
        if schedule_data.status is not None:
            schedule.status = schedule_data.status
        if schedule_data.priority is not None:
            schedule.priority = schedule_data.priority
        if schedule_data.metadata is not None:
            schedule.extra_data = schedule_data.metadata

        db.commit()
        db.refresh(schedule)
        return schedule

    @staticmethod
    def update_schedule_status(
        db: Session,
        schedule_id: int,
        user_id: int,
        status: str
    ) -> Optional[Schedule]:
        """Update schedule status"""
        schedule = db.query(Schedule).filter(
            and_(Schedule.id == schedule_id, Schedule.user_id == user_id)
        ).first()

        if not schedule:
            return None

        schedule.status = status
        db.commit()
        db.refresh(schedule)
        return schedule

    @staticmethod
    def delete_schedule(db: Session, schedule_id: int, user_id: int) -> bool:
        """Delete a schedule"""
        schedule = db.query(Schedule).filter(
            and_(Schedule.id == schedule_id, Schedule.user_id == user_id)
        ).first()

        if not schedule:
            return False

        db.delete(schedule)
        db.commit()
        return True

    @staticmethod
    def get_schedules_by_status(
        db: Session,
        user_id: int,
        status: str
    ) -> List[Schedule]:
        """Get schedules by status"""
        return db.query(Schedule).filter(
            and_(Schedule.user_id == user_id, Schedule.status == status)
        ).all()
