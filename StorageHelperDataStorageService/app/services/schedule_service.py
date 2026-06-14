"""
Schedule service layer for business logic
"""
import logging
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_
import copy
from datetime import datetime
from typing import List, Optional
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate

logger = logging.getLogger(__name__)


def _collect_dish_names(extra_data: dict) -> list:
    """Return a flat list of all dish names inside a schedule's extra_data JSON.

    Used for diagnostic logging when a dish-name lookup fails.
    """
    names = []
    for feat in (extra_data or {}).get("features", []):
        if feat.get("type") != "meal_plan":
            continue
        for day_plan in feat.get("plans", []):
            for meal in day_plan.get("meals", []):
                for dish in meal.get("dishes", []):
                    n = dish.get("name")
                    if n:
                        names.append(n)
    return names


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
        """
        Get schedules within a date range for a user.
        
        Uses simple two-phase approach:
        - Phase 1: Query regular schedules by time overlap
        - Phase 2: Query all meal plan schedules and filter in Python
        """
        # Convert datetime range to date for meal plan comparison
        start_date = start_time.date()
        end_date = end_time.date()
        
        # Phase 1: Get regular schedules with time overlap
        regular_schedules = db.query(Schedule).filter(
            and_(
                Schedule.user_id == user_id,
                or_(
                    and_(Schedule.scheduled_time >= start_time, Schedule.scheduled_time <= end_time),
                    and_(Schedule.end_time >= start_time, Schedule.end_time <= end_time),
                    and_(Schedule.scheduled_time <= start_time, Schedule.end_time >= end_time),
                )
            )
        ).all()
        
        # Phase 2: Get all meal plan schedules and filter by meal dates in Python
        meal_plan_schedules = db.query(Schedule).filter(
            and_(
                Schedule.user_id == user_id,
                Schedule.event_type.in_(['meal_plan_draft', 'meal_plan', 'shopping_list']),
                Schedule.extra_data.isnot(None)
            )
        ).all()
        
        # Filter meal plans that have at least one date in range
        filtered_meal_plans = []
        for schedule in meal_plan_schedules:
            # Skip if already in regular_schedules
            if schedule in regular_schedules:
                continue
            
            # Check if any meal date falls within range
            metadata = schedule.extra_data or {}
            features = metadata.get('features', [])
            
            has_date_in_range = False
            for feature in features:
                if feature.get('type') == 'meal_plan':
                    plans = feature.get('plans', [])
                    for day_plan in plans:
                        date_str = day_plan.get('date')
                        if date_str:
                            try:
                                # Parse date string (YYYY-MM-DD)
                                meal_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                                if start_date <= meal_date <= end_date:
                                    has_date_in_range = True
                                    break
                            except (ValueError, TypeError):
                                continue
                    if has_date_in_range:
                        break
            
            if has_date_in_range:
                filtered_meal_plans.append(schedule)
        
        # Combine and deduplicate
        all_schedules = list(regular_schedules) + filtered_meal_plans
        
        # Remove duplicates by ID and sort by scheduled_time
        seen_ids = set()
        unique_schedules = []
        for schedule in all_schedules:
            if schedule.id not in seen_ids:
                seen_ids.add(schedule.id)
                unique_schedules.append(schedule)
        
        unique_schedules.sort(key=lambda s: s.scheduled_time)
        
        return unique_schedules

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
    def _apply_ingredient_updates(dish: dict, ingredients: List[dict]) -> None:
        """Merge ingredient quantity updates into a dish object in-place.

        Matches by exact name first, then case-insensitive substring, so a partial
        name like "酱油" will match "生抽酱油" if no exact match exists.
        """
        if not ingredients:
            return
        ing_map = {i.get("name", "").strip().lower(): i for i in ingredients}
        for existing in dish.get("ingredients", []):
            existing_name = existing.get("name", "").strip().lower()
            # Exact match
            if existing_name in ing_map:
                existing["quantity"] = ing_map[existing_name]["quantity"]
                continue
            # Substring match
            for key, upd in ing_map.items():
                if key in existing_name or existing_name in key:
                    existing["quantity"] = upd["quantity"]
                    break

    @staticmethod
    def update_cooking_steps(
        db: Session,
        schedule_id: int,
        user_id: int,
        dish_name: str,
        steps: List[str],
        date: Optional[str] = None,
        meal_time: Optional[str] = None,
        ingredients: Optional[List[dict]] = None,
    ) -> Optional[Schedule]:
        """Update cooking steps (and optionally ingredient quantities) for a specific
        dish within a schedule's meal plan.

        Searches all meal_plan features for dishes matching `dish_name`
        (optionally filtered by `date` and `meal_time`) and replaces their
        `cookingSteps` field.  If `ingredients` is provided, also updates matching
        ingredient quantities.  Returns the updated schedule, or None if not found.
        """
        schedule = db.query(Schedule).filter(
            and_(Schedule.id == schedule_id, Schedule.user_id == user_id)
        ).first()

        if not schedule:
            logger.error(
                f"[ScheduleService.update_cooking_steps] SAVE_FAILED reason=schedule_not_found "
                f"schedule_id={schedule_id} user_id={user_id} dish='{dish_name}'"
            )
            return None

        if not schedule.extra_data:
            logger.error(
                f"[ScheduleService.update_cooking_steps] SAVE_FAILED reason=no_extra_data "
                f"schedule_id={schedule_id} user_id={user_id} dish='{dish_name}'"
            )
            return None

        extra_data = copy.deepcopy(schedule.extra_data)
        updated = False

        for feature in extra_data.get("features", []):
            if feature.get("type") != "meal_plan":
                continue
            for day_plan in feature.get("plans", []):
                if date and day_plan.get("date") != date:
                    continue
                for meal in day_plan.get("meals", []):
                    if meal_time and meal.get("mealTime") != meal_time:
                        continue
                    for dish in meal.get("dishes", []):
                        if dish.get("name", "").strip().lower() == dish_name.strip().lower():
                            dish["cookingSteps"] = steps
                            ScheduleService._apply_ingredient_updates(dish, ingredients or [])
                            updated = True

        if not updated:
            # Fallback 1: match by date + meal_time only (name may differ due to LLM reformatting)
            if date or meal_time:
                for feature in extra_data.get("features", []):
                    if feature.get("type") != "meal_plan":
                        continue
                    for day_plan in feature.get("plans", []):
                        if date and day_plan.get("date") != date:
                            continue
                        for meal in day_plan.get("meals", []):
                            if meal_time and meal.get("mealTime") != meal_time:
                                continue
                            dishes = meal.get("dishes", [])
                            if dishes:
                                # Update the first dish in this slot
                                dishes[0]["cookingSteps"] = steps
                                ScheduleService._apply_ingredient_updates(dishes[0], ingredients or [])
                                updated = True
                                logger.info(
                                    f"[ScheduleService] update_cooking_steps fallback: "
                                    f"updated first dish '{dishes[0].get('name')}' "
                                    f"at {date}/{meal_time} (requested: '{dish_name}')"
                                )
                                break
                        if updated:
                            break
                    if updated:
                        break

            # Fallback 2: fuzzy name match anywhere in the schedule (substring)
            if not updated:
                dish_lower = dish_name.strip().lower()
                for feature in extra_data.get("features", []):
                    if feature.get("type") != "meal_plan":
                        continue
                    for day_plan in feature.get("plans", []):
                        for meal in day_plan.get("meals", []):
                            for dish in meal.get("dishes", []):
                                stored = dish.get("name", "").strip().lower()
                                if stored in dish_lower or dish_lower in stored:
                                    dish["cookingSteps"] = steps
                                    ScheduleService._apply_ingredient_updates(dish, ingredients or [])
                                    updated = True
                                    logger.info(
                                        f"[ScheduleService] update_cooking_steps fuzzy match: "
                                        f"'{dish.get('name')}' ~= '{dish_name}'"
                                    )
                                    break
                            if updated:
                                break
                        if updated:
                            break
                    if updated:
                        break

        if not updated:
            logger.error(
                f"[ScheduleService.update_cooking_steps] SAVE_FAILED reason=dish_not_found_in_schedule "
                f"schedule_id={schedule_id} user_id={user_id} dish='{dish_name}' "
                f"date={date!r} meal_time={meal_time!r} "
                f"available_dishes={_collect_dish_names(extra_data)}"
            )
            return None

        schedule.extra_data = extra_data
        flag_modified(schedule, "extra_data")
        try:
            db.commit()
            db.refresh(schedule)
        except Exception as e:
            logger.error(
                f"[ScheduleService.update_cooking_steps] SAVE_FAILED reason=db_commit_error "
                f"schedule_id={schedule_id} user_id={user_id} dish='{dish_name}' error={e}"
            )
            db.rollback()
            return None
        logger.info(
            f"[ScheduleService.update_cooking_steps] SAVED schedule_id={schedule_id} dish='{dish_name}' "
            f"steps={len(steps)} ingredients_updated={ingredients is not None}"
        )
        return schedule

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
