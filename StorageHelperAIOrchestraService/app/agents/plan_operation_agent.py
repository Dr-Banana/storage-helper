"""
PlanOperationAgent: Operation type classification for PLAN_AHEAD modifications.
Defines PlanOperationType enum and get_operation_type() utility used by the chat
pipeline and scheduling agent to route add/modify/remove/multi/ingredients operations.
"""
from enum import Enum
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PlanOperationType(str, Enum):
    """Operation type for plan modifications."""
    ADD = "add"
    MODIFY = "modify"
    REMOVE = "remove"
    VIEW = "view"
    MULTI = "multi"
    REMOVE_INGREDIENTS = "remove_ingredients"
    UPDATE_INGREDIENTS = "update_ingredients"


def get_operation_type(mod_intent: Optional[Dict[str, Any]]) -> PlanOperationType:
    """Determine operation type from mod_intent."""
    if not mod_intent:
        return PlanOperationType.VIEW
    if isinstance(mod_intent.get("operations"), list):
        return PlanOperationType.MULTI
    op = (mod_intent.get("operation") or "").lower()
    if op == "add":
        return PlanOperationType.ADD
    if op == "modify":
        return PlanOperationType.MODIFY
    if op == "remove":
        return PlanOperationType.REMOVE
    if op == "remove_ingredients":
        return PlanOperationType.REMOVE_INGREDIENTS
    if op == "update_ingredients":
        return PlanOperationType.UPDATE_INGREDIENTS
    return PlanOperationType.VIEW
