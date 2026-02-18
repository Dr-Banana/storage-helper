import logging
import httpx
import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from app.modules.intent_classifier import intent_classifier, Intent
from app.modules.plan_ahead_state import get_plan_state, update_plan_state, clear_plan_state
from app.pipelines.intent_router import route_by_intent
from app.core.config import settings

logger = logging.getLogger(__name__)

# Import scheduling agents and plan operation routing
try:
    from app.agents.scheduling_agent import (
        ScheduleSessionContext,
        SchedulingResponseGenerator,
        PlanAheadAgent,
    )
    from app.agents.plan_operation_agent import get_operation_type, PlanOperationType
    SCHEDULING_AGENTS_AVAILABLE = True
    logger.info("Scheduling agents imported successfully")
except ImportError as e:
    logger.warning(f"Scheduling agents not available: {e}")
    SCHEDULING_AGENTS_AVAILABLE = False
    get_operation_type = None
    PlanOperationType = None

# Global flag: disable ALL schedule fetching in chat pipeline for now.
# Fetch logic will be redesigned and moved fully into scheduling agents.
ENABLE_SCHEDULE_FETCH_IN_CHAT = True

class ChatPipeline:
    """
    Pipeline for handling user chat interactions.
    """

    SYSTEM_PROMPT = """
You are a helpful and proactive Home AI Agent named "Storage Helper". 
You assist users with managing their home life, specifically focused on kitchen inventory, meal planning, and document organization.

Current Intent: {intent}
Reasoning: {reasoning}

If the intent is SEARCH: Acknowledge that you are looking for their items or documents.
If the intent is UPDATE: The system has searched for candidates to update. Present these candidates to the user and ASK FOR CONFIRMATION on which one to update and what values to change. DO NOT update until confirmed.
If the intent is CORRECTION_MODE (user is viewing a list): You can help them FIX existing items or ADD missing items to the list.
If the intent is PLAN_EAT_OUT: Suggest you can help find restaurants or make reservations.
If the intent is PLAN_AHEAD: Help the user plan meals for a future period or a specific date (e.g. next Monday), then generate a shopping list of ingredients to buy. Offer to save the list to their schedule when ready. Plan Cook Home is a sub-flow: when the user is cooking at home, use the provided USER'S ACTUAL INVENTORY (if any) to suggest recipes — only suggest recipes using ingredients that are ACTUALLY in the inventory; never invent ingredients not in the list. If inventory is empty or limited, say so and suggest what to buy.
  - When the user says they want to cook at home on a date but DON'T KNOW what to cook: FIRST have a short dialogue to decide — ask preferences (cuisine, dietary restrictions, difficulty, number of people), suggest 2–3 options, and let the user pick or refine. AFTER they confirm the dish and date, THEN generate the meal plan and shopping list (ingredients to buy) and use PLAN_JSON when you output the final plan.
  - When the user already has a plan or knows what to cook: proceed to generate/update meal_plan and shopping_list, and output PLAN_JSON when appropriate.
If the intent is GENERAL: Be friendly and helpful.

Respond naturally in the same language as the user.
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    def _extract_from_feature_format(self, metadata: Dict) -> Dict:
        """
        Extract simple meal plan and shopping list from Feature format.
        
        Input (Feature format):
            {
                "features": [
                    {
                        "type": "meal_plan",
                        "plans": [
                            {
                                "date": "2026-02-10",
                                "meals": [
                                    {
                                        "mealTime": "dinner",
                                        "dishes": [
                                            {
                                                "name": "Pasta",
                                                "ingredients": [{"name": "pasta"}, {"name": "tomato"}]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        
        Output (simple format for in-memory state):
            {
                "meal_plan": {"2026-02-10": "Pasta"},
                "shopping_list": ["pasta", "tomato"]
            }
        """
        meal_plan = {}
        shopping_list_set = set()
        
        features = metadata.get("features", [])
        for feature in features:
            if feature.get("type") == "meal_plan":
                plans = feature.get("plans", [])
                for day_plan in plans:
                    date = day_plan.get("date")
                    meals = day_plan.get("meals", [])
                    
                    # Collect all dish names for this date
                    dish_names = []
                    for meal in meals:
                        for dish in meal.get("dishes", []):
                            dish_name = dish.get("name", "").strip()
                            if dish_name:
                                dish_names.append(dish_name)
                            
                            # Collect ingredients
                            for ingredient in dish.get("ingredients", []):
                                ing_name = ingredient.get("name", "").strip()
                                if ing_name:
                                    shopping_list_set.add(ing_name)
                    
                    # Combine dish names into single meal text
                    if dish_names:
                        meal_plan[date] = ", ".join(dish_names)
        
        return {
            "meal_plan": meal_plan,
            "shopping_list": sorted(list(shopping_list_set))
        }
    
    async def _extract_plan_from_text(
        self,
        response_text: str,
        user_input: str,
        history: Optional[List[Dict[str, str]]],
        user_timezone: Optional[str] = None,
        current_meal_plan: Optional[Dict[str, str]] = None,
        current_shopping_list: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract meal_plan and shopping_list from LLM response text using another LLM call.
        Used as fallback when PLAN_JSON marker is not found.
        
        Returns:
            Dict with meal_plan and shopping_list, or None if extraction failed
        """
        # Calculate next week's dates for reference (use user's local timezone)
        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = today + timedelta(days=days_ahead)
        week_dates = {
            "Monday": next_monday,
            "Tuesday": next_monday + timedelta(days=1),
            "Wednesday": next_monday + timedelta(days=2),
            "Thursday": next_monday + timedelta(days=3),
            "Friday": next_monday + timedelta(days=4),
            "Saturday": next_monday + timedelta(days=5),
            "Sunday": next_monday + timedelta(days=6),
        }
        date_ref = ", ".join([f"{day}={date.strftime('%Y-%m-%d')}" for day, date in week_dates.items()])
        
        # Include current plan state in prompt
        current_state = ""
        if current_meal_plan or current_shopping_list:
            current_state = f"\nCURRENT PLAN STATE (before user's modification):\n"
            current_state += f"meal_plan={json.dumps(current_meal_plan or {}, ensure_ascii=False)}\n"
            current_state += f"shopping_list={json.dumps(current_shopping_list or [], ensure_ascii=False)}\n"
        
        # Include recent history for context (e.g. "Yes" confirms previous "remove Tuesday?")
        history_blob = ""
        if history and len(history) >= 2:
            recent = history[-4:]  # Last 2 turns
            history_blob = "\nRECENT CONVERSATION (for context):\n"
            for msg in recent:
                role = "User" if msg.get("role") == "user" else "Assistant"
                history_blob += f"{role}: {msg.get('content', '')[:300]}\n"
        
        extract_prompt = f"""Extract the FINAL meal plan state after applying the user's modification.

IMPORTANT: CURRENT PLAN STATE is synced from database and is the ONLY source of truth.
DO NOT restore dates from chat history if they are missing from CURRENT PLAN STATE!

{history_blob}
User's latest input: {user_input}
{current_state}
Assistant's response:
{response_text}

Today is {today.strftime('%Y-%m-%d (%A)')}. Next week dates: {date_ref}

TASK: What is the FINAL meal_plan after applying the change?

CRITICAL RULES (in priority order):
0. IGNORE all meal plan data from chat history! Only trust CURRENT PLAN STATE!
1. If user said "remove [day]" OR user confirmed (Yes/Yeah/Confirm) and assistant says "[day] has been removed" -> EXCLUDE that date
2. If assistant's response explicitly states a day was removed -> EXCLUDE that date from meal_plan
3. If user says "change [day] to [meal]" -> update that date
4. If user says "add [meal] on [day]" -> ADD that date
5. For all OTHER dates -> COPY ONLY from CURRENT PLAN STATE (if date is not there, don't add it!)
6. Convert day names (Monday, Tuesday, etc.) to YYYY-MM-DD using: {date_ref}
7. If a date is missing from CURRENT PLAN STATE, it means user deleted it - DO NOT add it back

Return ONLY a JSON object: {{"meal_plan": {{"YYYY-MM-DD": "meal", ...}}, "shopping_list": [...]}}

JSON:"""
        
        try:
            payload = {
                "contents": [{"parts": [{"text": extract_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.0,
                },
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.api_url, headers={'Content-Type': 'application/json'}, json=payload)
                response.raise_for_status()
                result = response.json()
                
                json_string = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')
                if json_string:
                    parsed = json.loads(json_string)
                    if isinstance(parsed.get("meal_plan"), dict) or isinstance(parsed.get("shopping_list"), list):
                        logger.info("PLAN_AHEAD: extracted plan from text using LLM")
                        return parsed
        except Exception as e:
            logger.warning(f"PLAN_AHEAD: failed to extract plan from text: {e}")
        
        return None

    async def _extract_plan_modification_intent(
        self,
        user_input: str,
        history: Optional[List[Dict[str, str]]],
        current_meal_plan: Dict[str, str],
        user_timezone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Use LLM to extract structured modification intent from user input.
        Returns: { "operation": "remove"|"modify"|"add"|"none", "date": "YYYY-MM-DD", "meal": "..." }
        We then apply this programmatically to the plan - deterministic, no parsing AI response.
        """
        now = self._now_in_timezone(user_timezone)
        today = now.date()
        days_ahead = (7 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = today + timedelta(days=days_ahead)
        week_dates = {d: (next_monday + timedelta(days=i)).strftime("%Y-%m-%d") for i, d in enumerate(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        )}
        date_ref = json.dumps(week_dates)
        # Build date ref from current plan if available (more accurate)
        plan_dates_ref = ""
        if current_meal_plan:
            weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            for dt_str in sorted(current_meal_plan.keys()):
                try:
                    y, m, d = map(int, dt_str.split("-"))
                    wd = weekdays[datetime(y, m, d).date().weekday()]
                    plan_dates_ref += f"{dt_str} ({wd}), "
                except (ValueError, IndexError):
                    pass
            if plan_dates_ref:
                date_ref = f"Dates in current plan: {plan_dates_ref.rstrip(', ')}. Fallback: {date_ref}"
        history_blob = ""
        if history and len(history) >= 2:
            recent = history[-4:]
            history_blob = "\nRecent conversation:\n"
            for msg in recent:
                role = "User" if msg.get("role") == "user" else "Assistant"
                history_blob += f"{role}: {msg.get('content', '')[:250]}\n"

        prompt = f"""You are extracting meal plan modification intent. Current date: {today.strftime('%Y-%m-%d')}.
Date reference (weekday -> YYYY-MM-DD): {date_ref}
Current plan: {json.dumps(current_meal_plan, ensure_ascii=False)}
{history_blob}
User's latest input: "{user_input}"

Determine the modification intent. Return JSON only.
- operation: "remove" | "modify" | "add" | "none"
- date: "YYYY-MM-DD" (required if operation is remove/modify/add; use date reference)
- meal: string (required only for modify/add; the meal name)
- append: boolean (true if user wants to ADD ANOTHER dish to existing date; false to replace)

CRITICAL RULES:
1. If user says "and [another dish]" or "also [dish]" or "add another [dish]" -> append=true, operation="add"
2. If user says "[day] will be [dish]" -> append=false, operation="modify" (replace)
3. If user says "[dish] on [day]" without context -> append=false, operation="add"
4. Infer the date from conversation context if not explicitly mentioned

Examples:
- "remove tuesday" -> {{"operation":"remove","date":"2026-02-10","meal":null,"append":false}}
- "change thursday to pizza" -> {{"operation":"modify","date":"2026-02-12","meal":"Pizza","append":false}}
- "add pasta on friday" -> {{"operation":"add","date":"2026-02-13","meal":"Pasta","append":false}}
- "and i want to do another lamb dish" (after discussing Monday) -> {{"operation":"add","date":"2026-02-09","meal":"Lamb dish","append":true}}
- "monday will be one beef and one lamb" -> {{"operation":"modify","date":"2026-02-09","meal":"one beef and one lamb","append":false}}
- "what's my plan" -> {{"operation":"none","date":null,"meal":null,"append":false}}

JSON:"""

        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.api_url, headers={"Content-Type": "application/json"}, json=payload)
                resp.raise_for_status()
                data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not text:
                return None
            parsed = json.loads(text)
            op = parsed.get("operation")
            if op and op != "none":
                date_val = parsed.get("date")
                if date_val:
                    return parsed
            return None
        except Exception as e:
            logger.warning(f"PLAN_AHEAD: extract modification intent failed: {e}")
            return None

    def _apply_plan_modification(
        self,
        meal_plan: Dict[str, str],
        shopping_list: List[str],
        intent: Dict[str, Any],
    ) -> Dict[str, str]:
        """Apply modification intent to meal_plan. Returns new meal_plan dict."""
        plan = dict(meal_plan)
        op = intent.get("operation")
        date = intent.get("date")
        meal = intent.get("meal")
        append = intent.get("append", False)  # Whether to append or replace
        
        if op == "remove" and date:
            plan.pop(date, None)
        elif op == "modify" and date and meal:
            # If date exists and append=True, append the new meal
            if date in plan and append:
                existing = plan[date]
                # Append with " and " separator
                plan[date] = f"{existing} and {meal}"
            else:
                # Replace or add
                plan[date] = meal
        elif op == "add" and date and meal:
            # "add" operation always appends if date exists
            if date in plan:
                existing = plan[date]
                plan[date] = f"{existing} and {meal}"
            else:
                plan[date] = meal
        
        return plan

    def _try_detect_removal_from_response(
        self,
        response_text: str,
        user_input: str,
        current_meal_plan: Dict[str, str],
        current_shopping_list: List[str],
        user_timezone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Programmatic fallback: when AI says "Tuesday has been removed" but no PLAN_JSON,
        detect the removed day and apply it. Handles confirmation flow (user: "Yes").
        """
        if not current_meal_plan:
            return None
        text = (response_text + " " + user_input).lower()
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        removed_day = None
        if "removed" not in text and "remove" not in text and "now open" not in text:
            return None
        idx_key = text.find("removed") if "removed" in text else (text.find("remove") if "remove" in text else text.find("now open"))
        # Find weekday closest to "removed" (e.g. "removed ... thursday" or "thursday ... removed")
        best_day, best_dist = None, 999
        for day in weekdays:
            if day not in text:
                continue
            idx_day = text.find(day)
            dist = abs(idx_day - idx_key)
            if dist < best_dist:
                best_dist, best_day = dist, day
        removed_day = best_day
        if not removed_day:
            return None
        day_idx = weekdays.index(removed_day)
        date_to_remove = None
        for dt_str in current_meal_plan:
            try:
                y, m, d = map(int, dt_str.split("-"))
                dt = datetime(y, m, d).date()
                if dt.weekday() == day_idx:
                    date_to_remove = dt_str
                    break
            except (ValueError, IndexError):
                continue
        if not date_to_remove:
            return None
        new_meal_plan = {k: v for k, v in current_meal_plan.items() if k != date_to_remove}
        logger.info(f"PLAN_AHEAD: programmatic removal detected - removed {date_to_remove} ({removed_day})")
        return {"meal_plan": new_meal_plan, "shopping_list": current_shopping_list}

    @staticmethod
    def _extract_ingredients_from_response_text(response_text: str) -> List[str]:
        """
        Extract ingredient list from LLM response when it mentions ingredients in natural language
        but PLAN_JSON had empty shopping_list. Handles patterns like:
        - "I have added Eggs, Tomatoes, and Scallions to your list"
        - "Shopping List: Eggs, Tomatoes, and Scallions"
        - "added X, Y, and Z to your list"
        """
        if not (response_text and response_text.strip()):
            return []
        text = response_text.strip()
        # Patterns: "added ... to your list" or "Shopping List: ..." or "ingredients: ..."
        for pattern in (
            r"(?:added|I have added)\s+([^.]*?)\s+to (?:your )?list",
            r"Shopping List:\s*([^.\n]+)",
            r"ingredients?:\s*([^.\n]+)",
            r"(?:added|add)\s+([^.]*?)\s+to (?:the )?list",
        ):
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if m:
                raw = m.group(1).strip()
                # Split by comma and " and "
                parts = re.split(r"\s*,\s*|\s+and\s+", raw, flags=re.IGNORECASE)
                items = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]
                if items:
                    return items
        return []

    def _now_in_timezone(self, user_timezone: Optional[str] = None) -> datetime:
        """Return current datetime in user's timezone, or UTC if not provided."""
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_timezone) if user_timezone else None
        except Exception:
            tz = None
        if tz:
            return datetime.now(tz)
        return datetime.utcnow()

    async def run(
        self,
        user_input: str,
        owner_id: int,
        history: List[Dict[str, str]] = None,
        context: Optional[Dict[str, Any]] = None,
        user_timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Runs the chat pipeline: Classify intent -> Route/Mock Action -> Generate response.
        """
        # 0. Check for context-based intent (e.g. Correction)
        # If we have explicit correction context, we bypass standard intent classification
        is_correction = False
        if context and context.get("type") == "correction" and context.get("data"):
            intent_result = type('obj', (object,), {'intent': Intent.UPDATE, 'reasoning': 'User is correcting items based on provided context', 'confidence': 1.0})
            intent_action = {'action': 'CORRECTION_MODE', 'data': {}, 'message': 'Processing correction...'}
            is_correction = True
        else:
            # 1. Classify intent (pass history so follow-up messages like "周一吃番茄炒蛋" stay PLAN_AHEAD)
            intent_result = await intent_classifier.classify(user_input, history=history)

            # 1b. Plan-ahead stickiness: if we're in a PLAN_AHEAD flow and classifier returned SEARCH/UPDATE
            #     for ambiguous phrases like "what i have", "what plan", "change monday", override to PLAN_AHEAD
            plan_state = get_plan_state(owner_id)
            in_plan_ahead_flow = (
                (context and context.get("type") == "plan_ahead")
                or (plan_state.get("meal_plan") or plan_state.get("shopping_list"))
                or (
                    history
                    and any(
                        kw in " ".join(m.get("content", "").lower() for m in history[-6:])
                        for kw in (
                            "meal plan", "next week", "monday", "tuesday", "planning", "shopping list",
                            "cook at home", "don't know what to cook", "decide what to cook", "what to cook",
                            "不知道做什么", "在家做饭", "做什么菜",
                            "同一天", "same day", "那天", "that day", "再加一个", "add another", "也加",
                        )
                    )
                )
            )
            ambiguous_plan_phrases = (
                "what i have",
                "what do i have",
                "what have",
                "what plan",
                "my plan",
                "current plan",
                "show plan",
                "show my plan",
                "change monday",
                "monday to",
                "wednesday change",
                "swap",
                "what do you recommend",
                "your recommendation",
                "something easy",
                "something simple",
                "decide for me",
                "你推荐",
                "随便",
                "同一天",
                "same day",
                "那天",
                "that day",
            )
            user_lower = user_input.strip().lower()
            is_ambiguous = any(phrase in user_lower for phrase in ambiguous_plan_phrases)
            if in_plan_ahead_flow and is_ambiguous and intent_result.intent in (Intent.SEARCH, Intent.UPDATE, Intent.GENERAL):
                old_intent = intent_result.intent
                intent_result = type(
                    "IntentResult",
                    (),
                    {
                        "intent": Intent.PLAN_AHEAD,
                        "reasoning": "User is in meal planning flow; ambiguous phrase interpreted as plan-related",
                        "confidence": intent_result.confidence,
                    },
                )()
                logger.info(f"Plan-ahead stickiness: overrode {old_intent} to PLAN_AHEAD for: {user_input[:50]}")

            # 2. Get intent-specific mock action/data (pass context for e.g. plan_ahead state)
            intent_action = await route_by_intent(
                intent_result.intent, user_input, owner_id, context=context
            )

        # 3. Generate response using Gemini
        # We include the detected intent and mock action in the prompt to guide the AI's response
        system_instruction = self.SYSTEM_PROMPT.format(
            intent=intent_result.intent.value,
            reasoning=intent_result.reasoning
        )
        
        # Add context about the specific action we're taking
        context_msg = f"\nSystem Action: {intent_action['message']}"
        # Track if this turn was a schedule lookup only (no plan-ahead modification). If so, we skip persistence in response.
        schedule_context_added_this_turn = False

        # Handle CORRECTION_MODE
        if is_correction:
             from app.pipelines.correction import correction_pipeline
             items = context.get("data", [])
             # Extract metadata from context if available
             metadata = context.get("metadata", {})
             
             # Call correction pipeline with metadata
             correction_result = await correction_pipeline.run(user_input, items, metadata if metadata else None)
             
             context_msg += f"\n\nCONTEXT: The user is viewing a list of {len(items)} items and wants to CORRECT them."
             context_msg += f"\nCURRENT LIST JSON: {json.dumps(items, indent=2)}"
             if metadata:
                 if metadata.get("ocr_text"):
                     context_msg += f"\nORIGINAL OCR TEXT AVAILABLE: {len(metadata.get('ocr_text', ''))} characters"
                 if metadata.get("vision_understanding"):
                     context_msg += "\nVISION UNDERSTANDING AVAILABLE: Yes"
             context_msg += f"\n\nCORRECTION RESULT: {json.dumps(correction_result, indent=2)}"
             context_msg += "\n\nCRITICAL: You have processed the correction."
             context_msg += "\n1. Summarize the changes you made in a friendly way."
             context_msg += "\n2. Ask the user to confirm by clicking the 'Apply Changes' button."
             
             intent_action['action'] = "APPLY_CORRECTION"
             intent_action['data'] = correction_result

        # Handle SEARCH intent results
        if intent_action['action'] == "SEARCH":
            if intent_action['data'].get('document_ids'):
                 document_ids = intent_action['data']['document_ids']
                 documents = intent_action['data'].get('documents', [])
                 num_found = len(document_ids)
                 
                 context_msg += f"\nSearch Results: Found exactly {num_found} document(s).\n"
                 context_msg += "Here are the details of the found items:\n"
                 
                 for idx, doc in enumerate(documents):
                     meta = doc.get("metadata", {})
                     # Try to find product name in various fields
                     # 1. First check document title (most reliable if set)
                     product_name = doc.get("title")
                     
                     # 2. Check metadata product_name/item_name
                     if not product_name:
                         product_name = meta.get("product_name") or meta.get("item_name")
                     
                     # 3. Fallback to OCR text if product name is missing
                     if not product_name:
                         ocr_text = doc.get("ocr_text", "")
                         if ocr_text:
                             # Use first line or first 30 chars of OCR text
                             product_name = ocr_text.split('\n')[0][:30]
                         else:
                             product_name = f"Document #{doc.get('id', 'Unknown')}"
                     
                     category = meta.get("category", "Unknown Category")
                     # Try to find location/storage info
                     # 1. First check document location_name (from DB relationship)
                     # 2. Then check metadata storage_condition/location
                     location = doc.get("location_name") or meta.get("storage_condition") or meta.get("location") or "Unknown Location"
                     qty = meta.get("quantity") or meta.get("quantity_unit") or "N/A"
                     expiry = meta.get("expiry_date") or meta.get("expiration_date") or "N/A"
                     
                     context_msg += f"{idx+1}. {product_name} [Category: {category}] [Location: {location}] [Qty: {qty}] [Expiry: {expiry}]\n"
                 
                 context_msg += "\nCRITICAL: You MUST construct your response based ONLY on the above search results. Do NOT invent items not listed here. If the details are generic (e.g. 'Document #123'), just describe what you see."
            else:
                 # No documents found
                 context_msg += "\nSearch Results: 0 documents found."
                 context_msg += "\nCRITICAL: The search returned NO results. You MUST explicitly state that no items matching the request were found in the inventory."
                 context_msg += "\nDO NOT invent or hallucinate any items. DO NOT say 'Here is what you have' if the list is empty."
        
        # Handle UPDATE intent results
        if intent_action['action'] == "UPDATE":
            if intent_action['data'].get('document_ids'):
                 document_ids = intent_action['data']['document_ids']
                 documents = intent_action['data'].get('documents', [])
                 num_found = len(document_ids)
                 changes = intent_action['data'].get('proposed_changes', {})
                 
                 context_msg += f"\nUpdate Candidates: Found {num_found} document(s) that might match the update request.\n"
                 if changes:
                     context_msg += f"Proposed Changes: {json.dumps(changes)}\n"
                 
                 context_msg += "Here are the details of the candidates:\n"
                 
                 for idx, doc in enumerate(documents):
                     meta = doc.get("metadata", {})
                     product_name = doc.get("title")
                     if not product_name:
                         product_name = meta.get("product_name") or meta.get("item_name")
                     if not product_name:
                         ocr_text = doc.get("ocr_text", "")
                         if ocr_text:
                             product_name = ocr_text.split('\n')[0][:30]
                         else:
                             product_name = f"Document #{doc.get('id', 'Unknown')}"
                     
                     category = meta.get("category", "Unknown Category")
                     location = doc.get("location_name") or meta.get("storage_condition") or meta.get("location") or "Unknown Location"
                     qty = meta.get("quantity") or meta.get("quantity_unit") or "N/A"
                     expiry = meta.get("expiry_date") or meta.get("expiration_date") or "N/A"
                     
                     context_msg += f"{idx+1}. {product_name} [Category: {category}] [Location: {location}] [Qty: {qty}] [Expiry: {expiry}] (ID: {doc.get('id', doc.get('document_id', 'Unknown'))})\n"
                 
                 context_msg += "\nCRITICAL: Tell the user to CLICK the 'UPDATE' button on the correct item below to confirm the changes."
                 context_msg += "\nDO NOT ask for text confirmation if the user can click."
                 context_msg += "\nSummarize what will be changed based on 'Proposed Changes'."
            else:
                 context_msg += "\nUpdate Search Results: 0 documents found."
                 context_msg += "\nTell the user you couldn't find the item they wanted to update."
        
        # PLAN_AHEAD: inject inventory from Plan Cook Home sub-agent (cook-at-home is a sub-flow of plan-ahead)
        if intent_action['action'] == "PLAN_AHEAD":
            try:
                from app.agents.agent_factory import agent_factory
                cook_home_result = await agent_factory.get_plan_cook_home_sub_agent().execute(
                    user_input=user_input, owner_id=owner_id
                )
                inventory_items = (cook_home_result.get("data") or {}).get("inventory_items", [])
                inventory_count = (cook_home_result.get("data") or {}).get("inventory_count", 0)
            except Exception as e:
                logger.warning(f"Plan Cook Home sub-agent failed (continuing without inventory): {e}")
                inventory_items = []
                inventory_count = 0
            if inventory_count > 0:
                context_msg += f"\n\nUSER'S ACTUAL INVENTORY ({inventory_count} items, from Plan Cook Home sub-agent):"
                context_msg += "\nCRITICAL: When suggesting recipes for cooking at home, use ONLY these actual ingredients."
                context_msg += "\nDO NOT hallucinate or invent ingredients not in this list.\n"
                items_by_category = {}
                item_count = 0
                max_items = 30
                for item in inventory_items:
                    if item_count >= max_items:
                        break
                    category = item.get('category', 'Other')
                    product_name = item.get('product_name', 'Unknown')
                    if category not in items_by_category:
                        items_by_category[category] = []
                    items_by_category[category].append(product_name)
                    item_count += 1
                for category, items in items_by_category.items():
                    context_msg += f"\n{category}:\n"
                    for item_name in items:
                        context_msg += f"  - {item_name}\n"
                if inventory_count > max_items:
                    context_msg += f"\n(Showing first {max_items} of {inventory_count} items)"
                context_msg += "\nWhen suggesting a recipe, list which items from the inventory above will be used."
            else:
                context_msg += "\n\nINVENTORY STATUS (Plan Cook Home): No food items found in the user's inventory."
                context_msg += "\nWhen suggesting recipes, acknowledge this and suggest what they might need to buy or upload a receipt first."

        # Handle schedule queries using SchedulingResponseGenerator
        # NOTE: schedule *fetch* (get_user_schedules) is temporarily disabled.
        # Time inference and context generation still work, but actual DB fetch is skipped.
        logger.info(
            f"[SCHEDULING AGENT] Checking if scheduling agents are available: {SCHEDULING_AGENTS_AVAILABLE}, "
            f"fetch_enabled={ENABLE_SCHEDULE_FETCH_IN_CHAT}"
        )
        if SCHEDULING_AGENTS_AVAILABLE:
            try:
                logger.info(f"[SCHEDULING AGENT] Processing query: {user_input[:100]}")
                logger.info(f"[SCHEDULING AGENT] User timezone: {user_timezone}")
                # Create session context from current query and history
                session_context = ScheduleSessionContext(
                    current_query=user_input,
                    history=history or [],
                    user_timezone=user_timezone,
                )
                
                # Initialize generator
                scheduling_generator = SchedulingResponseGenerator()
                
                # Log agent capabilities
                capabilities = scheduling_generator.get_agent_capabilities()
                logger.info(
                    f"[SCHEDULING AGENT] Agents available: {len(capabilities.get('agents', []))} agents"
                )
                logger.info(
                    f"[SCHEDULING AGENT] Capabilities: {capabilities.get('capabilities', [])}"
                )
                
                # Generate context to decide if we need to fetch schedules (LLM parses time when api_url provided)
                logger.info("[SCHEDULING AGENT] Calling generate_context to decide if schedules should be fetched")
                schedule_context = await scheduling_generator.generate_context(
                    context=session_context,
                    schedules=None,  # Will be fetched if needed
                    api_url=self.api_url,
                )
                
                logger.info(
                    f"[SCHEDULING AGENT] Decision result: should_fetch={schedule_context.get('should_fetch')}, "
                    f"has_time_range={schedule_context.get('time_range') is not None}"
                )
                
                # If we should fetch schedules, get them from storage
                if schedule_context.get("should_fetch") and schedule_context.get("time_range"):
                    time_range = schedule_context["time_range"]
                    logger.info(
                        f"[SCHEDULING AGENT] Should fetch schedules for range "
                        f"{time_range.start} to {time_range.end}, fetch_enabled={ENABLE_SCHEDULE_FETCH_IN_CHAT}"
                    )
                    
                    # Fetch schedules from storage via scheduling agent (by time range)
                    filtered_schedules = []
                    if ENABLE_SCHEDULE_FETCH_IN_CHAT:
                        from app.storage.pipeline_storage import _default_storage
                        filtered_schedules = await scheduling_generator.fetch_schedules_in_range(
                            owner_id=owner_id,
                            time_range=time_range,
                            storage_client=_default_storage,
                        )
                    else:
                        logger.info(
                            f"[SCHEDULING AGENT] Schedule fetch is disabled; skipping DB fetch, "
                            f"but time inference and context generation still work"
                        )
                    
                    # Only regenerate context if we found schedules OR if first call had no schedules
                    # If first call had schedules=None and we still have no schedules, reuse first result
                    if filtered_schedules:
                        # Found schedules: regenerate context with actual schedule data
                        logger.info(
                            f"[SCHEDULING AGENT] Regenerating context with {len(filtered_schedules)} fetched schedules, "
                            f"reusing time_range to avoid redundant computation"
                        )
                        schedule_context = await scheduling_generator.generate_context(
                            context=session_context,
                            schedules=filtered_schedules,
                            time_range=time_range,  # Reuse pre-computed time_range
                            api_url=self.api_url,
                        )
                    else:
                        # No schedules found: reuse first result (already has "no schedules" message)
                        logger.info(
                            f"[SCHEDULING AGENT] No schedules found, reusing first context result "
                            f"(no need to regenerate)"
                        )
                        # schedule_context already contains the correct "no schedules" message
                    
                    # Add schedule context to context_msg
                    if schedule_context.get("context_message"):
                        context_msg += schedule_context["context_message"]
                        schedule_context_added_this_turn = True
                        logger.info(
                            f"[SCHEDULING AGENT] Added schedule context to response "
                            f"(length: {len(schedule_context.get('context_message', ''))} chars)"
                        )
                    else:
                        logger.info("[SCHEDULING AGENT] No context message to add")
                else:
                    logger.info(
                        f"[SCHEDULING AGENT] No need to fetch schedules: "
                        f"should_fetch={schedule_context.get('should_fetch')}, "
                        f"has_time_range={schedule_context.get('time_range') is not None}"
                    )
            except Exception as e:
                logger.error(f"[SCHEDULING AGENT] Error: {e}", exc_info=True)
                # Continue without schedule context if there's an error
        else:
            logger.info("[SCHEDULING AGENT] Scheduling agents not available, skipping schedule query processing")

        if intent_action['action'] == "PLAN_AHEAD":
            # Use PlanAheadAgent to handle all PLAN_AHEAD logic
            if SCHEDULING_AGENTS_AVAILABLE:
                try:
                    from app.storage.pipeline_storage import _default_storage
                    plan_ahead_agent = PlanAheadAgent(gemini_api_url=self.api_url)
                    
                    # Get plan state: priority: context > server storage > database
                    plan_data = intent_action.get("data") or {}
                    server_state = get_plan_state(owner_id)
                    
                    # Sync from database using PlanAheadAgent
                    db_state = await plan_ahead_agent.sync_meal_plan_from_database(
                        owner_id=owner_id,
                        storage_client=_default_storage,
                    )
                    
                    # Update in-memory state only when we found data in database (so chat and calendar stay in sync)
                    # Use DB as source of truth for which dates exist: do NOT add server_state dates, so manually deleted schedules do not come back. For dates that exist in DB, merge slot details (db + server) so breakfast/lunch are preserved.
                    if db_state.get("schedule_id"):
                        db_mp = db_state.get("meal_plan") or {}
                        db_slots = db_state.get("meal_plan_slots") or {}
                        server_slots = server_state.get("meal_plan_slots") or {}
                        merged_slots = {d: {**(db_slots.get(d) or {}), **(server_slots.get(d) or {})} for d in db_mp}
                        db_di = db_state.get("dish_ingredients") or {}
                        server_di = server_state.get("dish_ingredients") or {}
                        merged_di = {}
                        for k in set(db_di) | set(server_di):
                            merged_di[k] = list(set((db_di.get(k) or []) + (server_di.get(k) or [])))
                        update_plan_state(
                            owner_id=owner_id,
                            meal_plan=db_mp,
                            shopping_list=db_state.get("shopping_list", []),
                            schedule_id=db_state.get("schedule_id"),
                            meal_plan_slots=merged_slots,
                            dish_ingredients=merged_di,
                            merge=False,
                        )
                        server_state = get_plan_state(owner_id)
                        logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Synced meal plan from schedule id={db_state.get('schedule_id')}: {len(db_mp)} meals")
                    # When DB has no schedule_id, keep in-memory state (e.g. same-session plan not yet persisted) so we can persist later
                    
                    # Use context if provided and has plan_ahead data, otherwise use server state
                    if context and context.get("type") == "plan_ahead" and isinstance(context.get("data"), dict):
                        meal_plan = context["data"].get("meal_plan") or server_state.get("meal_plan", {})
                        shopping_list = context["data"].get("shopping_list") or server_state.get("shopping_list", [])
                        meal_plan_slots = context["data"].get("meal_plan_slots") or server_state.get("meal_plan_slots", {})
                        dish_ingredients = context["data"].get("dish_ingredients") or server_state.get("dish_ingredients", {})
                    else:
                        meal_plan = plan_data.get("meal_plan") or server_state.get("meal_plan", {})
                        shopping_list = plan_data.get("shopping_list") or server_state.get("shopping_list", [])
                        meal_plan_slots = plan_data.get("meal_plan_slots") or server_state.get("meal_plan_slots", {})
                        dish_ingredients = plan_data.get("dish_ingredients") or server_state.get("dish_ingredients", {})
                    
                    # CRITICAL: Save the original meal_plan from database sync (before any modifications)
                    original_meal_plan_dates = set(meal_plan.keys()) if meal_plan else set()
                    
                    # 1. Extract modification intent from user input using PlanAheadAgent
                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD request handler: Before extract_modification_intent - meal_plan keys: {list(meal_plan.keys()) if meal_plan else []}")
                    mod_intent = await plan_ahead_agent.extract_modification_intent(
                        user_input=user_input,
                        history=history,
                        current_meal_plan=meal_plan,
                        user_timezone=user_timezone,
                        gemini_api_url=self.api_url,
                    )
                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD request handler: Extracted mod_intent: {mod_intent}")
                    
                    # Store mod_intent and operation type for later routing (add/modify/remove/multi only trigger apply)
                    intent_action["data"]["_mod_intent"] = mod_intent
                    op_type = get_operation_type(mod_intent) if get_operation_type else None
                    intent_action["data"]["_operation_type"] = op_type.value if (op_type and hasattr(op_type, "value")) else ("view" if not mod_intent else "add")
                    if op_type:
                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD operation type: {op_type.value} (only ADD/MODIFY/REMOVE/MULTI trigger apply)")
                    # Programmatic fallback: when user explicitly says 早饭/午饭/晚饭, set meal_time (overwrite LLM if it returned dinner)
                    if mod_intent and (user_input or "").strip():
                        def infer_meal_time(text: str) -> str:
                            if not text:
                                return "dinner"
                            t = text.lower()
                            if "早饭" in text or "早餐" in text or "早上" in text or "breakfast" in t or "morning" in t:
                                return "breakfast"
                            if "午饭" in text or "午餐" in text or "中午" in text or "lunch" in t:
                                return "lunch"
                            if "晚饭" in text or "晚餐" in text or "晚上" in text or "dinner" in t:
                                return "dinner"
                            return "dinner"
                        def meal_suggests_breakfast(meal_name: str) -> bool:
                            if not (meal_name or "").strip():
                                return False
                            m = (meal_name or "").lower().strip()
                            keywords = ("pancake", "waffle", "waffles", "煎饼", "松饼", "粥", "面包片", "吐司", "toast", "麦片", "cereal", "酸奶", "yogurt", "鸡蛋", "egg")
                            return any(k in m for k in keywords)
                        inferred = infer_meal_time(user_input or "")
                        # When user said breakfast/lunch, always force meal_time so we don't save to dinner
                        force_slot = inferred in ("breakfast", "lunch")
                        def resolve_meal_time_for_modify(op_date: str, new_meal: str, existing_slots: dict) -> str:
                            """For rename/modify: if date has exactly one slot, keep using that slot.
                            Otherwise fall back to inferred time or breakfast heuristic."""
                            if existing_slots and len(existing_slots) == 1:
                                return list(existing_slots.keys())[0]
                            if meal_suggests_breakfast(new_meal or ""):
                                return "breakfast"
                            return inferred

                        if isinstance(mod_intent.get("operations"), list):
                            for op in mod_intent["operations"]:
                                if op.get("operation") in ("add", "modify") and op.get("meal"):
                                    if force_slot:
                                        op["meal_time"] = inferred
                                    elif not op.get("meal_time"):
                                        if op.get("operation") == "modify" and op.get("date"):
                                            # Rename: keep the original slot instead of defaulting to dinner
                                            op["meal_time"] = resolve_meal_time_for_modify(
                                                op["date"], op.get("meal") or "",
                                                meal_plan_slots.get(op["date"]) or {}
                                            )
                                        elif meal_suggests_breakfast(op.get("meal") or ""):
                                            op["meal_time"] = "breakfast"
                                        else:
                                            op["meal_time"] = inferred
                        elif mod_intent.get("operation") in ("add", "modify") and mod_intent.get("meal"):
                            if force_slot:
                                mod_intent["meal_time"] = inferred
                            elif not mod_intent.get("meal_time"):
                                if mod_intent.get("operation") == "modify" and mod_intent.get("date"):
                                    # Rename: keep the original slot instead of defaulting to dinner
                                    mod_intent["meal_time"] = resolve_meal_time_for_modify(
                                        mod_intent["date"], mod_intent.get("meal") or "",
                                        meal_plan_slots.get(mod_intent["date"]) or {}
                                    )
                                elif meal_suggests_breakfast(mod_intent.get("meal") or ""):
                                    mod_intent["meal_time"] = "breakfast"
                                else:
                                    mod_intent["meal_time"] = inferred
                    # 2. Apply modification only when operation is ADD/MODIFY/REMOVE/MULTI or REMOVE_INGREDIENTS/UPDATE_INGREDIENTS (not VIEW)
                    # Ingredients-only: PlanIngredientsAgent; else dispatch to PlanAdd/Modify/Remove/Multi
                    _skip_apply = op_type and PlanOperationType and op_type == PlanOperationType.VIEW
                    if mod_intent and not _skip_apply:
                        op_type_val = op_type.value if hasattr(op_type, "value") else op_type
                        if op_type in (PlanOperationType.REMOVE_INGREDIENTS, PlanOperationType.UPDATE_INGREDIENTS):
                            from app.agents.plan_ingredients_agent import PlanIngredientsAgent
                            ingredients_agent = PlanIngredientsAgent()
                            dish_name = (mod_intent.get("dish") or "").strip()
                            if dish_name:
                                if op_type == PlanOperationType.REMOVE_INGREDIENTS:
                                    dish_ingredients, shopping_list = ingredients_agent.apply_remove_ingredients(
                                        dish_name, dish_ingredients, shopping_list
                                    )
                                    intent_action["data"]["dish_ingredients"] = dish_ingredients
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD: Applied ingredients op ({op_type_val}) for dish '{dish_name}'")
                                else:
                                    # UPDATE_INGREDIENTS: merge new ingredients with existing (e.g. "加个葱" -> add 葱, keep 西红柿/鸡蛋)
                                    ing_list = mod_intent.get("ingredients") or []
                                    if ing_list:
                                        # Find existing ingredients for this dish (match by key)
                                        existing_ings = []
                                        for k, v in (dish_ingredients or {}).items():
                                            if (k or "").strip().lower() == (dish_name or "").strip().lower() or ((dish_name or "").strip().lower() in (k or "").strip().lower()):
                                                existing_ings = list(v or [])
                                                break
                                        merged_ings = list(dict.fromkeys(existing_ings + [x.strip() for x in ing_list if (x or "").strip()]))
                                        dish_ingredients, shopping_list = ingredients_agent.apply_set_ingredients(
                                            dish_name, merged_ings, dish_ingredients, shopping_list
                                        )
                                        intent_action["data"]["dish_ingredients"] = dish_ingredients
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD: Applied ingredients op ({op_type_val}) for dish '{dish_name}'")
                                    else:
                                        intent_action["data"]["_ingredients_skip_apply"] = True  # allow response PLAN_JSON to merge dish_ingredients
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD: update_ingredients for dish '{dish_name}' with no list (LLM may suggest in response)")
                            intent_action["data"]["_applied_modification"] = True
                        else:
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD request handler: Dispatching to operation agent (op={op_type_val}) - meal_plan keys: {list(meal_plan.keys()) if meal_plan else []}, values: {meal_plan}")
                            meal_plan, meal_plan_slots = plan_ahead_agent.execute_operation(
                                op_type=op_type_val,
                                mod_intent=mod_intent,
                                meal_plan=meal_plan,
                                meal_plan_slots=meal_plan_slots,
                                shopping_list=shopping_list,
                            )
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD request handler: After apply_plan_modification - meal_plan keys: {list(meal_plan.keys()) if meal_plan else []}, values: {meal_plan}")
                            intent_action["data"]["_applied_modification"] = True
                            # For multi-ops: also handle update_ingredients/remove_ingredients sub-ops
                            if isinstance(mod_intent.get("operations"), list):
                                from app.agents.plan_ingredients_agent import PlanIngredientsAgent as _PIA
                                _ing_agent = _PIA()
                                for _sub_op in mod_intent["operations"]:
                                    _sop_type = (_sub_op.get("operation") or "").strip()
                                    _sop_dish = (_sub_op.get("dish") or "").strip()
                                    if _sop_type == "remove_ingredients" and _sop_dish:
                                        dish_ingredients, shopping_list = _ing_agent.apply_remove_ingredients(
                                            _sop_dish, dish_ingredients, shopping_list)
                                        logger.info(f"[SCHEDULING AGENT] Multi-op: removed ingredients for '{_sop_dish}'")
                                    elif _sop_type == "update_ingredients" and _sop_dish:
                                        _sop_ing = [x.strip() for x in (_sub_op.get("ingredients") or []) if (x or "").strip()]
                                        if _sop_ing:
                                            _exist = []
                                            for k, v in (dish_ingredients or {}).items():
                                                if (k or "").strip().lower() == _sop_dish.lower():
                                                    _exist = list(v or [])
                                                    break
                                            dish_ingredients, shopping_list = _ing_agent.apply_set_ingredients(
                                                _sop_dish, list(dict.fromkeys(_exist + _sop_ing)), dish_ingredients, shopping_list)
                                            logger.info(f"[SCHEDULING AGENT] Multi-op: set ingredients for '{_sop_dish}': {_sop_ing}")
                                        else:
                                            # No ingredients specified: tell LLM to suggest them
                                            intent_action["data"]["_ingredients_skip_apply"] = True
                                            intent_action["data"]["_ingredients_skip_apply_dish"] = _sop_dish
                                            logger.info(f"[SCHEDULING AGENT] Multi-op: update_ingredients for '{_sop_dish}' with no list (LLM may suggest)")
                                intent_action["data"]["dish_ingredients"] = dish_ingredients

                    # Update intent_action data with current (possibly modified) state
                    # For ingredients operations, preserve dish_ingredients from intent_action if it was already set
                    intent_action["data"]["meal_plan"] = meal_plan
                    intent_action["data"]["meal_plan_slots"] = meal_plan_slots
                    intent_action["data"]["shopping_list"] = shopping_list
                    # Only update dish_ingredients if it wasn't already set by ingredients operation (to avoid overwriting with old value)
                    if "dish_ingredients" not in intent_action["data"] or intent_action["data"]["dish_ingredients"] is None:
                        intent_action["data"]["dish_ingredients"] = dish_ingredients
                    else:
                        # Sync local variable with updated value from intent_action
                        dish_ingredients = intent_action["data"]["dish_ingredients"]
                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD request handler: Final state stored in intent_action - meal_plan keys: {list(intent_action['data']['meal_plan'].keys()) if intent_action['data'].get('meal_plan') else []}")
                    
                    # Generate context message using PlanAheadAgent
                    context_msg += plan_ahead_agent.generate_plan_ahead_context(
                        meal_plan=meal_plan,
                        shopping_list=shopping_list,
                        user_timezone=user_timezone,
                        dish_ingredients=dish_ingredients,
                    )
                    # When the user asked to add ingredients for a dish but didn't specify any,
                    # give the LLM an explicit instruction so it generates them and puts them in PLAN_JSON
                    if intent_action.get("data", {}).get("_ingredients_skip_apply"):
                        _skip_dish = (
                            intent_action["data"].get("_ingredients_skip_apply_dish")
                            or (intent_action["data"].get("_mod_intent") or {}).get("dish")
                            or ""
                        )
                        if _skip_dish:
                            context_msg += f'\n\nCRITICAL INGREDIENTS TASK: The user wants to ADD INGREDIENTS for "{_skip_dish}" but did not list any specific ingredients.'
                            context_msg += f'\nYOU MUST:'
                            context_msg += f'\n  1. Suggest typical/classic ingredients for "{_skip_dish}" in your response text'
                            context_msg += f'\n  2. MANDATORY — include them in PLAN_JSON under dish_ingredients: {{"{_skip_dish}": ["ing1", "ing2", ...]}}'
                            context_msg += f'\n  3. Add these same ingredients to shopping_list in PLAN_JSON'
                            context_msg += f'\n  Do NOT output PLAN_JSON without dish_ingredients for this request. This is required.'
                except Exception as e:
                    logger.error(f"[SCHEDULING AGENT] PlanAheadAgent error: {e}", exc_info=True)
                    # Fall back to old behavior if PlanAheadAgent fails
                    logger.warning("[SCHEDULING AGENT] Falling back to inline PLAN_AHEAD logic")
                    # Keep old inline code as fallback (will be removed after testing)
            else:
                logger.warning("[SCHEDULING AGENT] PlanAheadAgent not available, PLAN_AHEAD functionality disabled")
        elif intent_action.get('data', {}).get('suggestion'):
            context_msg += f"\nSuggestion: {intent_action['data']['suggestion']}"
        
        # Lookup vs persistence: when this turn was only a schedule lookup (no plan modification), skip PLAN_AHEAD parse+persist in response
        schedule_lookup_only_this_turn = (
            schedule_context_added_this_turn
            and intent_action.get("action") == "PLAN_AHEAD"
            and not intent_action.get("data", {}).get("_applied_modification")
        )
        if schedule_lookup_only_this_turn:
            logger.info("[SCHEDULING AGENT] This turn is schedule lookup only; will skip PLAN_AHEAD parse/persist in response")
        system_instruction += context_msg

        # 4. Final results to return to frontend
        final_result = {
            "response": "", # Placeholder
            "intent": intent_result.intent,
            "confidence": intent_result.confidence,
            "reasoning": intent_result.reasoning,
            "action": intent_action['action'],
            "action_data": intent_action['data']
        }

        # Build chat history if provided
        contents = []
        if history:
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        
        # Add current user input
        contents.append({"role": "user", "parts": [{"text": user_input}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 8192  # Enough for full PLAN_JSON (meal_plan + shopping_list + dish_ingredients)
            }
        }

        headers = {'Content-Type': 'application/json'}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()

                # Check for errors in response first
                if 'error' in result:
                    error_msg = result['error'].get('message', str(result['error']))
                    logger.error(f"Gemini API returned error: {error_msg}")
                    raise ValueError(f"Gemini API error: {error_msg}")

                # Check if candidates exist
                candidates = result.get('candidates', [])
                if not candidates:
                    logger.error(f"Gemini API response has no candidates. Full response: {result}")
                    raise ValueError("Gemini API response missing candidates.")

                # Check for finish reason (might indicate why content is missing)
                finish_reason = candidates[0].get('finishReason', '')
                if finish_reason and finish_reason != 'STOP':
                    logger.warning(f"Gemini API finished with reason: {finish_reason}")

                # Extract text content
                content = candidates[0].get('content', {})
                parts = content.get('parts', [])
                
                if not parts:
                    # Handle MAX_TOKENS case - response was truncated
                    if finish_reason == 'MAX_TOKENS':
                        logger.warning("Gemini API response truncated due to max tokens limit. Consider increasing maxOutputTokens.")
                        # Try to provide a fallback message
                        response_text = "我的回答可能因为长度限制而被截断了。让我为您提供一个更简洁的建议："
                        # Note: In this case, we'll return the fallback message
                        # but ideally we should increase maxOutputTokens to prevent this
                    else:
                        logger.error(f"Gemini API response has no parts in content. Finish reason: {finish_reason}, Full response: {result}")
                        raise ValueError(f"Gemini API response missing parts in content (finish reason: {finish_reason}).")
                else:
                    response_text = parts[0].get('text', '')
                
                if not response_text:
                    logger.error(f"Gemini API response missing text content. Finish reason: {finish_reason}, Full response keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")
                    raise ValueError("Gemini API response missing text content.")

                # When schedule lookup only: do not parse or persist; strip PLAN_JSON from display if present
                if schedule_lookup_only_this_turn and intent_action.get("action") == "PLAN_AHEAD" and "PLAN_JSON:" in response_text:
                    response_text = response_text[: response_text.find("PLAN_JSON:")].rstrip()
                
                # PLAN_AHEAD: parse PLAN_JSON from response and merge into action_data; strip from user-visible response
                # Skip entirely when this turn was schedule lookup only (lookup and persistence must not overlap)
                if intent_action['action'] == "PLAN_AHEAD" and SCHEDULING_AGENTS_AVAILABLE and not schedule_lookup_only_this_turn:
                    try:
                        from app.storage.pipeline_storage import _default_storage
                        plan_ahead_agent = PlanAheadAgent(gemini_api_url=self.api_url)
                        
                        # Get current meal plan state and operation type (ADD/MODIFY/REMOVE/VIEW/MULTI)
                        current_meal_plan = intent_action["data"].get("meal_plan", {})
                        current_shopping_list = intent_action["data"].get("shopping_list", [])
                        mod_intent = intent_action["data"].get("_mod_intent")
                        op_type = intent_action["data"].get("_operation_type") or (get_operation_type(mod_intent).value if get_operation_type else "view")
                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: operation_type={op_type}, applied_mod={intent_action['data'].get('_applied_modification')}")
                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Initial state - current_meal_plan keys: {list(current_meal_plan.keys())}, mod_intent: {mod_intent}")
                        
                        # For add operation: check if target date already has a plan, if yes skip, if no merge with existing schedule and save
                        # For modify/remove operations: fetch complete meal_plan from database to merge properly
                        db_meal_plan_base = {}
                        db_shopping_list_base = []
                        db_meal_plan_slots_for_remove = {}
                        target_schedule_id = None
                        add_date_has_existing_plan = False  # Initialize variable - will be set based on database check
                        
                        if intent_action["data"].get("_applied_modification") and mod_intent:
                            # Check if this is multiple operations format
                            operations_list = mod_intent.get("operations") if isinstance(mod_intent.get("operations"), list) else None
                            if operations_list:
                                # Handle multiple operations
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD: Processing {len(operations_list)} operations")
                                try:
                                    all_schedules = await _default_storage.get_user_schedules(owner_id)
                                    
                                    # 定义内部函数提取计划
                                    def extract_meal_plan_from_schedule(schedule):
                                        meta = schedule.get("metadata") or {}
                                        if not isinstance(meta, dict): return {}, []
                                        schedule_mp = {}
                                        schedule_sl = meta.get("shopping_list") or []
                                        
                                        if isinstance(meta.get("meal_plan"), dict):
                                            schedule_mp = meta.get("meal_plan")
                                        elif isinstance(meta.get("features"), list):
                                            for feat in meta.get("features"):
                                                if isinstance(feat, dict) and feat.get("type") == "meal_plan":
                                                    for plan in feat.get("plans", []):
                                                        if plan.get("date") and plan.get("meals"):
                                                            dish_names = [d.get("name") for m in plan["meals"] for d in m.get("dishes", []) if d.get("name")]
                                                            if dish_names: schedule_mp[plan["date"]] = " and ".join(dish_names)
                                        elif isinstance(meta.get("features"), dict):
                                            schedule_mp = meta.get("features").get("meal_plan") or {}
                                        
                                        return schedule_mp, schedule_sl
                                    
                                    plan_state = get_plan_state(owner_id)
                                    multi_day_schedule_id = None
                                    
                                    # Process each operation: remove operations first, then add/modify
                                    sorted_ops = sorted(operations_list, key=lambda x: 0 if x.get("operation") == "remove" else 1)
                                    
                                    for op_intent in sorted_ops:
                                        op = op_intent.get("operation")
                                        op_date = op_intent.get("date")
                                        op_meal = op_intent.get("meal")
                                        
                                        if op == "remove" and op_date:
                                            # Only delete schedule when removing entire date; if removing a specific dish (op_meal), just update slots later
                                            if op_meal:
                                                continue
                                            # Delete single-day schedules containing this date
                                            for s in all_schedules:
                                                mp, _ = extract_meal_plan_from_schedule(s)
                                                if op_date not in mp:
                                                    continue
                                                sid = s.get("id")
                                                if len(mp) == 1:
                                                    ok = await _default_storage.delete_schedule(sid, owner_id)
                                                    if ok:
                                                        logger.info(f"[SCHEDULING AGENT] Deleted single-day schedule id={sid} for date {op_date} (multi-op)")
                                                        if plan_state.get("schedule_id") == sid:
                                                            update_plan_state(owner_id=owner_id, schedule_id=None, merge=True)
                                                else:
                                                    if multi_day_schedule_id is None:
                                                        multi_day_schedule_id = sid
                                                        logger.info(f"[SCHEDULING AGENT] REMOVE (multi-op): will update multi-day schedule id={sid} after removing date {op_date}")
                                        
                                        elif op in ["add", "modify"] and op_date:
                                            # Find schedule containing this date for add/modify
                                            for s in all_schedules:
                                                mp, _ = extract_meal_plan_from_schedule(s)
                                                if op_date in mp:
                                                    if multi_day_schedule_id is None:
                                                        multi_day_schedule_id = s.get("id")
                                                        logger.info(f"[SCHEDULING AGENT] {op.upper()} (multi-op): will update schedule id={s.get('id')} for date {op_date}")
                                                    break
                                    
                                    if multi_day_schedule_id:
                                        target_schedule_id = multi_day_schedule_id
                                    
                                except Exception as e:
                                    logger.warning(f"[SCHEDULING AGENT] Multi-op DB operations failed: {e}", exc_info=True)
                            
                            # Single operation (backward compatible) - only process if not multiple operations
                            if not operations_list:
                                operation = mod_intent.get("operation")
                                target_date = mod_intent.get("date")
                                if operation and target_date:
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD: Processing {operation} on {target_date}")
                                
                                    try:
                                        all_schedules = await _default_storage.get_user_schedules(owner_id)
                                        
                                        # 定义内部函数提取计划
                                        def extract_meal_plan_from_schedule(schedule):
                                            meta = schedule.get("metadata") or {}
                                            if not isinstance(meta, dict): return {}, []
                                            schedule_mp = {}
                                            schedule_sl = meta.get("shopping_list") or []
                                            
                                            if isinstance(meta.get("meal_plan"), dict):
                                                schedule_mp = meta.get("meal_plan")
                                            elif isinstance(meta.get("features"), list):
                                                for feat in meta.get("features"):
                                                    if isinstance(feat, dict) and feat.get("type") == "meal_plan":
                                                        for plan in feat.get("plans", []):
                                                            if plan.get("date") and plan.get("meals"):
                                                                dish_names = [d.get("name") for m in plan["meals"] for d in m.get("dishes", []) if d.get("name")]
                                                                if dish_names: schedule_mp[plan["date"]] = " and ".join(dish_names)
                                            elif isinstance(meta.get("features"), dict):
                                                schedule_mp = meta.get("features").get("meal_plan") or {}
                                            
                                            return schedule_mp, schedule_sl

                                        # 策略 1: 优先寻找已经包含目标日期的 Schedule
                                        target_schedule = None
                                        if target_date:
                                            for s in all_schedules:
                                                mp, _ = extract_meal_plan_from_schedule(s)
                                                if target_date in mp:
                                                    target_schedule = s
                                                    logger.info(f"[SCHEDULING AGENT] Found existing schedule id={s.get('id')} containing date {target_date}")
                                                    break
                                        
                                        # Check if target date already has a plan in database (for add operation)
                                        if operation == "add" and target_date:
                                            add_date_has_existing_plan = target_schedule is not None
                                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD: ADD operation - target date {target_date} has existing plan in DB: {add_date_has_existing_plan}")
                                        
                                        # REMOVE: delete schedules only when removing entire date; if removing a specific dish (meal), skip delete and update slots at persist
                                        if operation == "remove" and target_date and not mod_intent.get("meal"):
                                            removed_date = target_date
                                            plan_state = get_plan_state(owner_id)
                                            for s in all_schedules:
                                                mp, _ = extract_meal_plan_from_schedule(s)
                                                if removed_date not in mp:
                                                    continue
                                                sid = s.get("id")
                                                if len(mp) == 1:
                                                    ok = await _default_storage.delete_schedule(sid, owner_id)
                                                    if ok:
                                                        logger.info(f"[SCHEDULING AGENT] Deleted single-day schedule id={sid} for date {removed_date}")
                                                        if plan_state.get("schedule_id") == sid:
                                                            update_plan_state(owner_id=owner_id, schedule_id=None, merge=True)
                                                else:
                                                    if target_schedule_id is None:
                                                        target_schedule_id = sid
                                                        logger.info(f"[SCHEDULING AGENT] REMOVE: will update multi-day schedule id={sid} after removing date {removed_date}")
                                        
                                        # 策略 2: 对于 add/modify 操作，如果目标日期没有计划，不合并到现有 schedule，而是创建新的
                                        # 只有在目标日期已经有计划的情况下，才合并到现有 schedule
                                        if not target_schedule and operation != "remove":
                                            # 对于 add/modify 操作，如果目标日期没有计划，不合并到现有 schedule
                                            if operation in ["add", "modify"]:
                                                # 检查目标日期是否在任何 schedule 中
                                                target_date_in_any_schedule = False
                                                for s in all_schedules:
                                                    mp, _ = extract_meal_plan_from_schedule(s)
                                                    if target_date in mp:
                                                        target_date_in_any_schedule = True
                                                        target_schedule = s
                                                        logger.info(f"[SCHEDULING AGENT] Found schedule id={s.get('id')} containing target date {target_date} for {operation} operation")
                                                        break
                                                
                                                # 如果目标日期不在任何 schedule 中，不合并到现有 schedule，让后续逻辑创建新的 schedule
                                                if not target_date_in_any_schedule:
                                                    logger.info(f"[SCHEDULING AGENT] Target date {target_date} not in any schedule, will create new schedule for {operation} operation")
                                            else:
                                                # 对于其他操作（非 add/modify），使用原来的逻辑
                                                drafts = [s for s in all_schedules if s.get("event_type") == "meal_plan_draft"]
                                                drafts.sort(key=lambda x: x.get("id"), reverse=True)
                                                if drafts:
                                                    target_schedule = drafts[0]
                                                    logger.info(f"[SCHEDULING AGENT] Using latest draft schedule id={target_schedule.get('id')} for new date")

                                        # 策略 3: 加载数据 (只加载目标 Schedule 的数据，不再合并所有！)
                                        if target_schedule:
                                            target_schedule_id = target_schedule.get("id")
                                            db_meal_plan_base, db_shopping_list_base = extract_meal_plan_from_schedule(target_schedule)
                                            try:
                                                _, _, _, db_meal_plan_slots_for_remove = _default_storage._extract_meal_plan_from_schedule(target_schedule)
                                            except Exception:
                                                pass
                                            
                                            # 合并逻辑：数据库(Base) + 内存中的修改(Current)
                                            # 注意：current_meal_plan 此时已经包含了 PlanAheadAgent 在内存中应用的新修改
                                            # 我们只需要把数据库里 *其它日期* 的数据补回来，不要覆盖掉 current 中已经修改的日期
                                            
                                            # 先把 DB 的数据作为底板
                                            merged_plan = db_meal_plan_base.copy()
                                            # 再把当前的修改覆盖上去
                                            merged_plan.update(current_meal_plan)
                                            
                                            current_meal_plan = merged_plan
                                            
                                            # 购物车列表简单合并去重
                                            if db_shopping_list_base:
                                                current_shopping_list = list(set(db_shopping_list_base + (current_shopping_list or [])))
                                                
                                            logger.info(f"[SCHEDULING AGENT] Merged result: {len(current_meal_plan)} days. Target ID: {target_schedule_id}")

                                    except Exception as e:
                                        logger.warning(f"[SCHEDULING AGENT] DB Sync failed: {e}", exc_info=True)
                        
                        # 1. Try to parse PLAN_JSON from response
                        parsed_plan = plan_ahead_agent.parse_plan_json(response_text)
                        if parsed_plan:
                            # Strip the PLAN_JSON line from what we show the user
                            idx = response_text.find("PLAN_JSON:")
                            if idx >= 0:
                                response_text = response_text[:idx].rstrip()
                        
                        # 2. If no PLAN_JSON found, try programmatic removal detection
                        if parsed_plan is None:
                            parsed_plan = plan_ahead_agent.try_detect_removal_from_response(
                                response_text, user_input, current_meal_plan, current_shopping_list, user_timezone
                            )
                        
                        # 3. If still no plan, try LLM extraction
                        if parsed_plan is None:
                            parsed_plan = await plan_ahead_agent.extract_plan_from_text(
                                response_text=response_text,
                                user_input=user_input,
                                history=history,
                                user_timezone=user_timezone,
                                current_meal_plan=current_meal_plan,
                                current_shopping_list=current_shopping_list,
                                gemini_api_url=self.api_url,
                            )
                        # 4. Update action_data and server state only if plan changed or we applied a modification
                        if parsed_plan or intent_action["data"].get("_applied_modification"):
                            applied_mod = intent_action["data"].get("_applied_modification")
                            _ingredients_only_op = (intent_action["data"].get("_operation_type") or "") in ("remove_ingredients", "update_ingredients") and not intent_action["data"].get("_ingredients_skip_apply")
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Processing - applied_mod={applied_mod}, parsed_plan exists={parsed_plan is not None}")
                            if parsed_plan:
                                parsed_mp = parsed_plan.get("meal_plan") if isinstance(parsed_plan.get("meal_plan"), dict) else None
                                parsed_sl = parsed_plan.get("shopping_list") if isinstance(parsed_plan.get("shopping_list"), list) else None
                                # Fallback: if LLM mentioned ingredients in message but PLAN_JSON has empty shopping_list, extract from response text
                                if (parsed_sl is None or len(parsed_sl) == 0) and response_text:
                                    extracted = self._extract_ingredients_from_response_text(response_text)
                                    if extracted:
                                        parsed_sl = extracted
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Extracted ingredients from response text: {parsed_sl}")
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Parsed plan - parsed_mp keys: {list(parsed_mp.keys()) if parsed_mp else None}, parsed_mp values: {parsed_mp}, parsed_sl: {parsed_sl}")
                                # Only trust PLAN_JSON for dates in current plan or this turn's target date(s); ignore hallucinated dates
                                allowed_dates = set(current_meal_plan.keys()) if current_meal_plan else set()
                                if mod_intent:
                                    if isinstance(mod_intent.get("operations"), list):
                                        for op in mod_intent.get("operations", []):
                                            if op.get("date"):
                                                allowed_dates.add(op["date"])
                                    elif mod_intent.get("date"):
                                        allowed_dates.add(mod_intent["date"])
                                if parsed_mp and allowed_dates:
                                    parsed_mp = {k: v for k, v in parsed_mp.items() if k in allowed_dates}
                                # View-only subset: LLM echoed only the range we showed; merge and do not persist
                                if not applied_mod and parsed_mp is not None and current_meal_plan and set(parsed_mp.keys()) < set(current_meal_plan.keys()):
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: View-only subset detected, merging")
                                    intent_action["data"]["meal_plan"] = {**current_meal_plan, **parsed_mp}
                                    if parsed_sl is not None:
                                        intent_action["data"]["shopping_list"] = parsed_sl
                                    if not _ingredients_only_op and isinstance(parsed_plan.get("dish_ingredients"), dict):
                                        intent_action["data"]["dish_ingredients"] = parsed_plan["dish_ingredients"]
                                # Multiple operations: handle all operations (remove first, then add/modify)
                                if applied_mod and mod_intent and isinstance(mod_intent.get("operations"), list):
                                    operations_list = mod_intent.get("operations")
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: MULTIPLE operations - processing {len(operations_list)} operations")
                                    # Use parsed plan or current meal plan as base
                                    intent_action["data"]["meal_plan"] = current_meal_plan  # Already modified by apply_plan_modification
                                    if parsed_sl is not None:
                                        intent_action["data"]["shopping_list"] = list(set((current_shopping_list or []) + parsed_sl))
                                    else:
                                        intent_action["data"]["shopping_list"] = current_shopping_list
                                    if not _ingredients_only_op and isinstance(parsed_plan.get("dish_ingredients"), dict):
                                        intent_action["data"]["dish_ingredients"] = parsed_plan["dish_ingredients"]

                                # Add: if target date already has plan, treat as update-existing (merge dish and persist); otherwise merge and maybe create new
                                elif applied_mod and mod_intent and mod_intent.get("operation") == "add":
                                    if add_date_has_existing_plan:
                                        # Same day already has a plan: current_meal_plan already has merged "A and B" from apply_plan_modification. Update existing schedule.
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: ADD operation - target date already has plan, will UPDATE existing schedule with merged meal")
                                        intent_action["data"]["meal_plan"] = current_meal_plan
                                        intent_action["data"]["shopping_list"] = list(set((current_shopping_list or []) + (parsed_sl or [])))
                                        # Merge dish_ingredients and meal_plan_slots: existing from target_schedule + new from applied/parsed
                                        try:
                                            _, _, db_dish_ingredients, db_meal_plan_slots = _default_storage._extract_meal_plan_from_schedule(target_schedule)
                                            parsed_di = parsed_plan.get("dish_ingredients") if isinstance(parsed_plan.get("dish_ingredients"), dict) else {}
                                            db_di = db_dish_ingredients or {}
                                            merged_di = {}
                                            for k in set(db_di) | set(parsed_di):
                                                merged_di[k] = list(set((db_di.get(k) or []) + (parsed_di.get(k) or [])))
                                            if not _ingredients_only_op:
                                                intent_action["data"]["dish_ingredients"] = merged_di
                                            # For target date use only cur_slots (applied state) so we don't resurrect slots user deleted from that day in DB. For other dates merge db + cur.
                                            cur_slots = intent_action["data"].get("meal_plan_slots") or {}
                                            target_date = mod_intent.get("date")
                                            merged_slots = {}
                                            for d in set((db_meal_plan_slots or {}).keys()) | set(cur_slots.keys()):
                                                if d == target_date:
                                                    merged_slots[d] = dict(cur_slots.get(d) or {})
                                                else:
                                                    merged_slots[d] = {**((db_meal_plan_slots or {}).get(d) or {}), **(cur_slots.get(d) or {})}
                                            intent_action["data"]["meal_plan_slots"] = merged_slots
                                        except Exception:
                                            if not _ingredients_only_op and isinstance(parsed_plan.get("dish_ingredients"), dict):
                                                intent_action["data"]["dish_ingredients"] = parsed_plan["dish_ingredients"]
                                        # target_schedule_id already set above; persist_meal_plan will use it to update
                                    else:
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: ADD operation - target date has no plan, merging with existing schedule")
                                        # Merge existing plan (if any) with new item
                                        intent_action["data"]["meal_plan"] = current_meal_plan  # Already merged above if existing schedule found
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: ADD operation - merged result keys: {list(intent_action['data']['meal_plan'].keys())}, values: {intent_action['data']['meal_plan']}")
                                        if parsed_sl is not None:
                                            intent_action["data"]["shopping_list"] = list(set((current_shopping_list or []) + parsed_sl))
                                        if not _ingredients_only_op and isinstance(parsed_plan.get("dish_ingredients"), dict):
                                            intent_action["data"]["dish_ingredients"] = parsed_plan["dish_ingredients"]

                                        # If no existing schedule was found, create a new schedule directly
                                        target_date = mod_intent.get("date")
                                        meal = mod_intent.get("meal")
                                        if target_date and meal and not target_schedule_id:
                                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: ADD operation - no existing schedule found, creating new schedule for date {target_date}")
                                            try:
                                                new_schedule_id = await plan_ahead_agent.create_schedule_for_add(
                                                    owner_id=owner_id,
                                                    date=target_date,
                                                    meal=meal,
                                                    user_timezone=user_timezone,
                                                )
                                                if new_schedule_id:
                                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Successfully created schedule id={new_schedule_id} for add operation")
                                                    intent_action["data"]["schedule_id"] = new_schedule_id
                                                    intent_action["data"]["_new_schedule_created_this_turn"] = True
                                                    target_schedule_id = new_schedule_id
                                                    # Update plan state with new schedule ID
                                                    update_plan_state(owner_id=owner_id, schedule_id=new_schedule_id, merge=True)
                                            except Exception as e:
                                                logger.error(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Failed to create schedule for add: {e}", exc_info=True)
                                # Modify: overlay only changed date(s) on existing plan
                                elif applied_mod and mod_intent and mod_intent.get("operation") == "modify":
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: MODIFY operation - merging")
                                    intent_action["data"]["meal_plan"] = {**current_meal_plan, **(parsed_mp or {})}
                                    parsed_di = parsed_plan.get("dish_ingredients") if isinstance(parsed_plan.get("dish_ingredients"), dict) else None
                                    if not _ingredients_only_op and parsed_di:
                                        # Merge with existing dish_ingredients so we keep other dishes' lists; parsed_di updates/adds the modified dish only
                                        current_di = intent_action["data"].get("dish_ingredients") or {}
                                        merged_di = {**current_di, **parsed_di}
                                        intent_action["data"]["dish_ingredients"] = merged_di
                                        from itertools import chain
                                        # When LLM returned only one dish in dish_ingredients (e.g. "蓝莓煎饼生成清单" -> only 蓝莓煎饼's list), show only that dish's ingredients in shopping_list
                                        if len(parsed_di) == 1:
                                            intent_action["data"]["shopping_list"] = sorted(set(next(iter(parsed_di.values()))))
                                        else:
                                            intent_action["data"]["shopping_list"] = sorted(set(chain(*merged_di.values())))
                                    else:
                                        if parsed_sl is not None:
                                            intent_action["data"]["shopping_list"] = parsed_sl
                                        if not _ingredients_only_op and isinstance(parsed_plan.get("dish_ingredients"), dict):
                                            intent_action["data"]["dish_ingredients"] = parsed_plan["dish_ingredients"]

                                    # If no existing schedule was found and target date doesn't have plan, create a new schedule directly
                                    target_date = mod_intent.get("date")
                                    meal = mod_intent.get("meal")
                                    modify_date_has_existing_plan = target_date in current_meal_plan if target_date else False
                                    if target_date and meal and not target_schedule_id and not modify_date_has_existing_plan:
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: MODIFY operation - target date {target_date} has no plan and no existing schedule, creating new schedule")
                                        try:
                                            new_schedule_id = await plan_ahead_agent.create_schedule_for_add(
                                                owner_id=owner_id,
                                                date=target_date,
                                                meal=meal,
                                                user_timezone=user_timezone,
                                            )
                                            if new_schedule_id:
                                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Successfully created schedule id={new_schedule_id} for modify operation")
                                                intent_action["data"]["schedule_id"] = new_schedule_id
                                                intent_action["data"]["_new_schedule_created_this_turn"] = True
                                                target_schedule_id = new_schedule_id
                                                # Update plan state with new schedule ID
                                                update_plan_state(owner_id=owner_id, schedule_id=new_schedule_id, merge=True)
                                        except Exception as e:
                                            logger.error(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Failed to create schedule for modify: {e}", exc_info=True)
                                # Remove: if removing entire date, drop it; if removing only a dish (meal), current_meal_plan already updated by apply_plan_modification
                                elif applied_mod and mod_intent and mod_intent.get("operation") == "remove":
                                    if mod_intent.get("meal"):
                                        # Remove one dish from one slot - keep current_meal_plan (already correct)
                                        intent_action["data"]["meal_plan"] = current_meal_plan
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: REMOVE dish - keeping slot-updated meal_plan")
                                    else:
                                        removed_date = mod_intent.get("date")
                                        logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: REMOVE operation - removing date {removed_date}")
                                        base = {k: v for k, v in current_meal_plan.items() if k != removed_date} if current_meal_plan else {}
                                        intent_action["data"]["meal_plan"] = {**base, **(parsed_mp or {})}
                                        # If parsed_mp brings removed_date back, restore meal_plan_slots from DB so lunch/dinner aren't flattened to dinner
                                        if (parsed_mp or {}).get(removed_date) and db_meal_plan_slots_for_remove.get(removed_date):
                                            cur_slots = dict(intent_action["data"].get("meal_plan_slots") or {})
                                            cur_slots[removed_date] = db_meal_plan_slots_for_remove.get(removed_date) or {}
                                            intent_action["data"]["meal_plan_slots"] = cur_slots
                                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Restored meal_plan_slots for {removed_date} from DB")
                                    if parsed_sl is not None:
                                        intent_action["data"]["shopping_list"] = parsed_sl
                                # update_ingredients/remove_ingredients: merge dish_ingredients from request handler (already applied) with parsed_plan if LLM returned it
                                elif applied_mod and mod_intent and mod_intent.get("operation") in ("update_ingredients", "remove_ingredients"):
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: {mod_intent.get('operation')} operation - preserving applied dish_ingredients")
                                    # Keep meal_plan and shopping_list from current state (ingredients op doesn't change meals)
                                    intent_action["data"]["meal_plan"] = current_meal_plan
                                    # Merge dish_ingredients: request handler already applied the change, merge with parsed_plan if LLM returned it
                                    current_di = intent_action["data"].get("dish_ingredients") or {}
                                    parsed_di = parsed_plan.get("dish_ingredients") if isinstance(parsed_plan.get("dish_ingredients"), dict) else {}
                                    if parsed_di:
                                        # Merge: parsed_di may have additional dishes or updates from LLM
                                        merged_di = {**current_di, **parsed_di}
                                        intent_action["data"]["dish_ingredients"] = merged_di
                                    # If parsed_sl exists, merge with current shopping_list
                                    if parsed_sl is not None:
                                        intent_action["data"]["shopping_list"] = list(set((current_shopping_list or []) + parsed_sl))
                                else:
                                    logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Default case - using parsed_mp directly")
                                    if not _ingredients_only_op:
                                        if parsed_mp is not None:
                                            intent_action["data"]["meal_plan"] = parsed_mp
                                        if parsed_sl is not None:
                                            intent_action["data"]["shopping_list"] = parsed_sl
                                        if isinstance(parsed_plan.get("dish_ingredients"), dict):
                                            intent_action["data"]["dish_ingredients"] = parsed_plan["dish_ingredients"]
                            
                            meal_plan_final = intent_action["data"].get("meal_plan", {})
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: meal_plan_final keys: {list(meal_plan_final.keys())}, values: {meal_plan_final}")
                            shopping_list_final = intent_action["data"].get("shopping_list", [])
                            dish_ingredients_final = intent_action["data"].get("dish_ingredients")
                            # Get current dish_ingredients from plan state for comparison
                            current_state = get_plan_state(owner_id)
                            current_dish_ingredients = current_state.get("dish_ingredients") or {}
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: dish_ingredients_final={dish_ingredients_final}, current_dish_ingredients={current_dish_ingredients}")
                            
                            # Only persist when user actually changed something or plan content changed (avoid re-PUT on view-only)
                            # For ingredients operations, also check if dish_ingredients changed
                            dish_ingredients_changed = dish_ingredients_final != current_dish_ingredients if dish_ingredients_final is not None else False
                            plan_changed = parsed_plan is not None and (
                                meal_plan_final != current_meal_plan or shopping_list_final != current_shopping_list or dish_ingredients_changed
                            )
                            # Do not persist when we merged a subset (view-only) to avoid wiping other weeks
                            merged_subset = not applied_mod and parsed_plan and isinstance(parsed_plan.get("meal_plan"), dict) and current_meal_plan and set(parsed_plan["meal_plan"].keys()) < set(current_meal_plan.keys())
                            # For ADD: skip persist only when we actually created a new schedule this turn (create_schedule_for_add). Updating existing schedule (add_date_has_existing_plan) must persist.
                            new_schedule_created = intent_action["data"].get("_new_schedule_created_this_turn", False)
                            should_persist = (applied_mod or plan_changed) and not merged_subset
                            
                            # Handle multiple operations: always persist (operations already handled in DB)
                            if applied_mod and mod_intent and isinstance(mod_intent.get("operations"), list):
                                should_persist = True  # Multiple operations always need persist
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Multiple operations - will persist final meal_plan")
                            elif applied_mod and mod_intent and mod_intent.get("operation") == "add" and add_date_has_existing_plan and not new_schedule_created:
                                should_persist = True  # Update existing schedule with merged meal (add-to-same-day)
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: ADD to existing day - will persist (update) schedule id={target_schedule_id}")
                            elif new_schedule_created and mod_intent and mod_intent.get("operation") != "remove":
                                # Only skip persist for add/modify operations when new schedule was created
                                # Remove operations always need persist to update the schedule
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: New schedule created, will update plan state but skip persist_meal_plan (already persisted)")
                                should_persist = False  # Skip persist_meal_plan since we already created the schedule
                            
                            # 5. Filter deleted dates using PlanAheadAgent
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Before filter_deleted_dates - meal_plan_final keys: {list(meal_plan_final.keys())}")
                            meal_plan_final = await plan_ahead_agent.filter_deleted_dates(
                                meal_plan_final=meal_plan_final,
                                mod_intent=mod_intent,
                                storage_client=_default_storage,
                                owner_id=owner_id,
                            )
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: After filter_deleted_dates - meal_plan_final keys: {list(meal_plan_final.keys())}, values: {meal_plan_final}")
                            
                            intent_action["data"]["meal_plan"] = meal_plan_final
                            
                            # 6. Update server state (replace completely, not merge); keep meal_plan_slots so next request has breakfast/lunch/dinner
                            meal_plan_slots_for_state = intent_action["data"].get("meal_plan_slots")
                            update_plan_state(
                                owner_id=owner_id,
                                meal_plan=meal_plan_final,
                                shopping_list=shopping_list_final,
                                meal_plan_slots=meal_plan_slots_for_state if meal_plan_slots_for_state is not None else {},
                                merge=False,  # Replace completely to allow removal
                            )

                            # 7. Persist to schedule only when plan was modified or meaningfully changed
                            logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: should_persist={should_persist}, plan_changed={plan_changed}, merged_subset={merged_subset}")
                            _why = "skip" if not should_persist else "persist"
                            _reason = []
                            if not (applied_mod or plan_changed):
                                _reason.append("not_applied_and_plan_unchanged")
                            if merged_subset:
                                _reason.append("merged_subset")
                            if new_schedule_created and mod_intent and mod_intent.get("operation") != "remove":
                                _reason.append("new_schedule_created_skip")
                            logger.info(f"[BACKEND] persist_decision: should_persist={should_persist}, why={_why}, reason={_reason}, applied_mod={applied_mod}, plan_changed={plan_changed}")
                            if should_persist:
                                plan_state = get_plan_state(owner_id)
                                existing_id = plan_state.get("schedule_id")
                                # [FIX] 使用上面逻辑找到的 ID，如果上面找到了更准确的 ID，优先使用它
                                if target_schedule_id: 
                                    existing_id = target_schedule_id
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Persisting meal_plan - existing_id={existing_id}, meal_plan_final keys: {list(meal_plan_final.keys())}, values: {meal_plan_final}")
                                meal_plan_slots_final = intent_action["data"].get("meal_plan_slots")
                                logger.info(f"[BACKEND] meal_plan_slots_final_passed_to_persist: existing_id={existing_id}, meal_plan_final_keys={list(meal_plan_final.keys()) if meal_plan_final else []}, meal_plan_slots_final={meal_plan_slots_final}")
                                schedule_id = await plan_ahead_agent.persist_meal_plan(
                                    meal_plan=meal_plan_final,
                                    shopping_list=shopping_list_final,
                                    owner_id=owner_id,
                                    existing_schedule_id=existing_id,
                                    storage_client=_default_storage,
                                    user_timezone=user_timezone,
                                    event_type="meal_plan_draft",
                                    dish_ingredients=dish_ingredients_final,
                                    meal_plan_slots=meal_plan_slots_final,
                                )
                                logger.info(f"[BACKEND] persist_result: schedule_id={schedule_id}, success={schedule_id is not None}")
                                logger.info(f"[SCHEDULING AGENT] PLAN_AHEAD response handler: Persist completed - schedule_id={schedule_id}")
                                if schedule_id:
                                    intent_action["data"]["schedule_id"] = schedule_id
                                    update_plan_state(owner_id=owner_id, schedule_id=schedule_id, dish_ingredients=intent_action["data"].get("dish_ingredients"), merge=True)
                                    
                                    # If this was a new addition (not just viewing), modify response to confirm save
                                    # Handle multiple operations: extract dates and meals from operations
                                    if applied_mod and mod_intent and isinstance(mod_intent.get("operations"), list):
                                        operations_list = mod_intent.get("operations")
                                        # Find add/modify operations to generate confirmation
                                        add_modify_ops = [op for op in operations_list if op.get("operation") in ["add", "modify"]]
                                        if add_modify_ops:
                                            # Use the first add/modify operation for confirmation message
                                            op_intent = add_modify_ops[0]
                                            target_date = op_intent.get("date")
                                            meal = op_intent.get("meal")
                                            if target_date and meal:
                                                import re
                                                # Format date nicely
                                                date_str = target_date
                                                try:
                                                    from datetime import datetime
                                                    date_obj = datetime.strptime(target_date, '%Y-%m-%d')
                                                    day = date_obj.day
                                                    month_name = date_obj.strftime('%B')
                                                    date_str = f"{month_name} {day}{'st' if day == 1 else 'nd' if day == 2 else 'rd' if day == 3 else 'th'}"
                                                except:
                                                    pass
                                                
                                                # Check if response mentions "already have" or similar - replace with confirmation
                                                already_patterns = [
                                                    r"I\s+see\s+that\s+I\s+already\s+have.*?\.\s*",
                                                    r"I\s+already\s+have.*?\.\s*",
                                                    r"already\s+scheduled.*?\.\s*",
                                                ]
                                                
                                                replaced = False
                                                for pattern in already_patterns:
                                                    if re.search(pattern, response_text, re.IGNORECASE | re.DOTALL):
                                                        confirmation = f"I've saved {meal} for {date_str}."
                                                        response_text = re.sub(
                                                            pattern,
                                                            confirmation + " ",
                                                            response_text,
                                                            flags=re.IGNORECASE | re.DOTALL,
                                                            count=1
                                                        )
                                                        logger.info(f"[SCHEDULING AGENT] Replaced 'already have' with confirmation: {confirmation}")
                                                        replaced = True
                                                        break
                                                
                                                # Check if LLM already generated a confirmation (to avoid duplication)
                                                confirmation_keywords = [
                                                    r"^(got\s+it|i've\s+(saved|set|added|recorded|scheduled)|perfect|done|all\s+set)",
                                                    r"i've\s+(saved|set|added|recorded|scheduled).*?" + re.escape(meal.lower()),
                                                    r"got\s+it.*?" + re.escape(meal.lower()),
                                                ]
                                                
                                                has_confirmation = False
                                                for pattern in confirmation_keywords:
                                                    if re.search(pattern, response_text, re.IGNORECASE):
                                                        has_confirmation = True
                                                        logger.info(f"[SCHEDULING AGENT] Response already contains confirmation, skipping duplicate")
                                                        break
                                                
                                                # Only add confirmation if:
                                                # 1. We didn't replace "already have" AND
                                                # 2. Response doesn't already have a confirmation
                                                if not replaced and not has_confirmation:
                                                    confirmation = f"I've saved {meal} for {date_str}. "
                                                    response_text = confirmation + response_text
                                                    logger.info(f"[SCHEDULING AGENT] Prepended confirmation to response: {confirmation}")
                                    elif applied_mod and mod_intent and mod_intent.get("operation") in ["add", "modify"]:
                                        target_date = mod_intent.get("date")
                                        meal = mod_intent.get("meal")
                                        if target_date and meal:
                                            import re
                                            # Format date nicely
                                            date_str = target_date
                                            try:
                                                from datetime import datetime
                                                date_obj = datetime.strptime(target_date, '%Y-%m-%d')
                                                day = date_obj.day
                                                if 4 <= day <= 20 or 24 <= day <= 30:
                                                    suffix = "th"
                                                else:
                                                    suffix = ["st", "nd", "rd"][day % 10 - 1]
                                                date_str = date_obj.strftime(f'%A, %B %d{suffix}')
                                            except:
                                                pass
                                            
                                            # Check if response mentions "already have" or similar - replace with confirmation
                                            already_patterns = [
                                                r"I\s+see\s+that\s+I\s+already\s+have.*?\.\s*",
                                                r"I\s+already\s+have.*?\.\s*",
                                                r"already\s+scheduled.*?\.\s*",
                                            ]
                                            
                                            replaced = False
                                            for pattern in already_patterns:
                                                if re.search(pattern, response_text, re.IGNORECASE | re.DOTALL):
                                                    confirmation = f"I've saved {meal} for {date_str}."
                                                    response_text = re.sub(
                                                        pattern, 
                                                        confirmation + " ",
                                                        response_text,
                                                        flags=re.IGNORECASE | re.DOTALL,
                                                        count=1
                                                    )
                                                    logger.info(f"[SCHEDULING AGENT] Replaced 'already have' with confirmation: {confirmation}")
                                                    replaced = True
                                                    break
                                            
                                            # Check if LLM already generated a confirmation (to avoid duplication)
                                            # Look for common confirmation phrases at the start of response
                                            confirmation_keywords = [
                                                r"^(got\s+it|i've\s+(saved|set|added|recorded|scheduled)|perfect|done|all\s+set)",
                                                r"i've\s+(saved|set|added|recorded|scheduled).*?" + re.escape(meal.lower()),
                                                r"got\s+it.*?" + re.escape(meal.lower()),
                                            ]
                                            
                                            has_confirmation = False
                                            for pattern in confirmation_keywords:
                                                if re.search(pattern, response_text, re.IGNORECASE):
                                                    has_confirmation = True
                                                    logger.info(f"[SCHEDULING AGENT] Response already contains confirmation, skipping duplicate")
                                                    break
                                            
                                            # Only add confirmation if:
                                            # 1. We didn't replace "already have" AND
                                            # 2. Response doesn't already have a confirmation
                                            if not replaced and not has_confirmation:
                                                confirmation = f"I've saved {meal} for {date_str}. "
                                                response_text = confirmation + response_text
                                                logger.info(f"[SCHEDULING AGENT] Prepended confirmation to response: {confirmation}")
                                elif existing_id and not meal_plan_final and not shopping_list_final:
                                    if "schedule_id" in intent_action.get("data", {}):
                                        del intent_action["data"]["schedule_id"]
                                    update_plan_state(owner_id=owner_id, schedule_id=None, merge=True)
                            else:
                                logger.info("[SCHEDULING AGENT] PlanAheadAgent: view-only response, skipping persist (no change)")

                        # 8. Handle SAVE_TO_SCHEDULE finalization
                        if "SAVE_TO_SCHEDULE" in response_text:
                            plan_data = intent_action.get("data") or {}
                            shopping_list = plan_data.get("shopping_list") or []
                            meal_plan = plan_data.get("meal_plan") or {}
                            if shopping_list or meal_plan:
                                plan_state = get_plan_state(owner_id)
                                existing_id = plan_data.get("schedule_id") or plan_state.get("schedule_id")
                                schedule_id = await plan_ahead_agent.persist_meal_plan(
                                    meal_plan=meal_plan,
                                    shopping_list=shopping_list,
                                    owner_id=owner_id,
                                    existing_schedule_id=existing_id,
                                    storage_client=_default_storage,
                                    user_timezone=user_timezone,
                                    event_type="shopping_list",
                                )
                                if schedule_id is not None:
                                    intent_action["data"]["schedule_id"] = schedule_id
                                    intent_action["data"]["saved_to_schedule"] = True
                                    update_plan_state(owner_id=owner_id, schedule_id=schedule_id, merge=True)
                                    logger.info(f"[SCHEDULING AGENT] PlanAheadAgent: Saved shopping list to schedule id={schedule_id}")
                            response_text = response_text.replace("SAVE_TO_SCHEDULE", "").strip()

                        # Clean internal flags before returning
                        if intent_action.get("data"):
                            intent_action["data"].pop("_applied_modification", None)
                            intent_action["data"].pop("_mod_intent", None)
                    except Exception as e:
                        logger.error(f"[SCHEDULING AGENT] PlanAheadAgent error in response processing: {e}", exc_info=True)
                        # Continue with response even if PlanAheadAgent fails

                return {
                    "response": response_text,
                    "intent": intent_result.intent,
                    "confidence": intent_result.confidence,
                    "reasoning": intent_result.reasoning,
                    "action": intent_action['action'],
                    "action_data": intent_action['data']
                }

        except Exception as e:
            logger.error(f"Chat generation failed: {e}")
            return {
                "response": "I'm sorry, I'm having some trouble processing that right now. Could you try again?",
                "intent": intent_result.intent,
                "confidence": intent_result.confidence,
                "reasoning": f"Generation error: {str(e)}",
                "action": "GENERAL",
                "action_data": {}
            }

# Singleton instance
chat_pipeline = ChatPipeline()

