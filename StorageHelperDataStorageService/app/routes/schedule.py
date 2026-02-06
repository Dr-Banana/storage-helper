"""
Schedule API routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleResponse, ScheduleStatusUpdate
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(
    schedule_data: ScheduleCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Create a new schedule"""
    schedule = ScheduleService.create_schedule(db, user_id, schedule_data)
    return ScheduleResponse.from_orm_object(schedule)


@router.get("", response_model=List[ScheduleResponse])
def get_schedules(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Get all schedules for current user"""
    schedules = ScheduleService.get_user_schedules(db, user_id)
    return [ScheduleResponse.from_orm_object(s) for s in schedules]


@router.get("/range", response_model=List[ScheduleResponse])
def get_schedules_by_range(
    start_time: datetime,
    end_time: datetime,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Get schedules within a date range"""
    schedules = ScheduleService.get_user_schedules_by_range(db, user_id, start_time, end_time)
    return [ScheduleResponse.from_orm_object(s) for s in schedules]


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Get a specific schedule"""
    schedule = ScheduleService.get_schedule(db, schedule_id, user_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return ScheduleResponse.from_orm_object(schedule)


@router.put("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    schedule_id: int,
    schedule_data: ScheduleUpdate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Update a schedule"""
    schedule = ScheduleService.update_schedule(db, schedule_id, user_id, schedule_data)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return ScheduleResponse.from_orm_object(schedule)


@router.patch("/{schedule_id}/status", response_model=ScheduleResponse)
def update_schedule_status(
    schedule_id: int,
    status_data: ScheduleStatusUpdate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Update schedule status"""
    schedule = ScheduleService.update_schedule_status(db, schedule_id, user_id, status_data.status)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return ScheduleResponse.from_orm_object(schedule)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Delete a schedule"""
    success = ScheduleService.delete_schedule(db, schedule_id, user_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return None
