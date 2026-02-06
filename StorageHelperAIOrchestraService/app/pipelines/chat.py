import logging
import httpx
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from app.modules.intent_classifier import intent_classifier, Intent
from app.modules.plan_ahead_state import get_plan_state, update_plan_state, clear_plan_state
from app.pipelines.intent_router import route_by_intent
from app.core.config import settings

logger = logging.getLogger(__name__)

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
If the intent is PLAN_COOK_HOME: Offer to generate recipes based on their ACTUAL inventory items. 
  - CRITICAL: Only suggest recipes using ingredients that are ACTUALLY in their inventory.
  - NEVER make up or hallucinate ingredients that are not in the provided inventory list.
  - If the inventory list is empty or limited, acknowledge this and suggest what they might need to buy.
If the intent is PLAN_AHEAD: Help the user plan meals for a future period (e.g. next week), decide what to cook each day, then generate a shopping list of ingredients to buy. Offer to save the shopping list to their schedule when they are ready.
If the intent is GENERAL: Be friendly and helpful.

Respond naturally in the same language as the user.
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

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
{history_blob}
User's latest input: {user_input}
{current_state}
Assistant's response:
{response_text}

Today is {today.strftime('%Y-%m-%d (%A)')}. Next week dates: {date_ref}

TASK: What is the FINAL meal_plan after applying the change?

RULES:
1. If user said "remove [day]" OR user confirmed (Yes/Yeah/Confirm) and assistant says "[day] has been removed" or "that day is now open" -> EXCLUDE that date
2. If assistant's response explicitly states a day was removed (e.g. "Tuesday has been removed", "plan for Tuesday... removed") -> EXCLUDE that date from meal_plan
3. If user says "change [day] to [meal]" -> update that date
4. If user says "add [meal] on [day]" -> include that date
5. For all OTHER dates -> COPY from CURRENT PLAN STATE, do not change them
6. Convert day names (Monday, Tuesday, etc.) to YYYY-MM-DD using: {date_ref}

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

Examples:
- "remove tuesday" -> {{"operation":"remove","date":"2026-02-10","meal":null}}
- "yes" (confirming assistant's "remove Tuesday?") -> {{"operation":"remove","date":"2026-02-10","meal":null}}
- "change thursday to pizza" -> {{"operation":"modify","date":"2026-02-12","meal":"Pizza"}}
- "add pasta on friday" -> {{"operation":"add","date":"2026-02-13","meal":"Pasta"}}
- "what's my plan" -> {{"operation":"none","date":null,"meal":null}}

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
        
        if op == "remove" and date:
            plan.pop(date, None)
        elif op == "modify" and date and meal:
            # Treat "modify" as "add" if date doesn't exist (user's intent to set a meal)
            plan[date] = meal
        elif op == "add" and date and meal:
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
                        kw in " ".join(m.get("content", "").lower() for m in history[-4:])
                        for kw in ("meal plan", "next week", "monday", "tuesday", "planning", "shopping list")
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
            )
            user_lower = user_input.strip().lower()
            is_ambiguous = any(phrase in user_lower for phrase in ambiguous_plan_phrases)
            if in_plan_ahead_flow and is_ambiguous and intent_result.intent in (Intent.SEARCH, Intent.UPDATE):
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
        
        if intent_action['action'] == "PLAN_COOK_HOME":
            # Add detailed inventory information for recipe suggestions
            inventory_items = intent_action.get('data', {}).get('inventory_items', [])
            inventory_count = intent_action.get('data', {}).get('inventory_count', 0)
            
            if inventory_count > 0:
                context_msg += f"\n\nUSER'S ACTUAL INVENTORY ({inventory_count} items):"
                context_msg += "\nCRITICAL: You MUST ONLY suggest recipes using these actual ingredients."
                context_msg += "\nDO NOT hallucinate or invent ingredients not in this list.\n"
                
                # Format inventory by category for better organization (limit to 30 items to avoid too long context)
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
                context_msg += "\n\nINVENTORY STATUS: No food items found in the user's inventory."
                context_msg += "\nAcknowledge this and suggest that they upload a shopping receipt to add items first."

        if intent_action['action'] == "PLAN_AHEAD":
            # Get plan state: priority: context > server storage > database
            plan_data = intent_action.get("data") or {}
            server_state = get_plan_state(owner_id)
            
            # If in-memory state is empty, try to load from database schedule (e.g. after container restart)
            if not server_state.get("meal_plan") and not server_state.get("shopping_list"):
                logger.info(f"Loading meal plan state from database for user {owner_id}")
                from app.storage.pipeline_storage import _default_storage
                schedules = await _default_storage.get_user_schedules(owner_id)
                for s in schedules:
                    if s.get("event_type") in ("meal_plan_draft", "shopping_list") and "Next Week" in (s.get("title") or ""):
                        metadata = s.get("metadata") or {}
                        if metadata.get("meal_plan") or metadata.get("shopping_list"):
                            server_state = {
                                "meal_plan": metadata.get("meal_plan", {}),
                                "shopping_list": metadata.get("shopping_list", []),
                                "schedule_id": s.get("id"),
                            }
                            logger.info(f"Restored meal plan from schedule id={s.get('id')}: {len(server_state.get('meal_plan', {}))} meals")
                            # Update in-memory state
                            update_plan_state(
                                owner_id=owner_id,
                                meal_plan=server_state.get("meal_plan"),
                                shopping_list=server_state.get("shopping_list"),
                                schedule_id=server_state.get("schedule_id"),
                                merge=False,
                            )
                            # Re-fetch plan_state to get updated schedule_id
                            plan_state = get_plan_state(owner_id)
                            break
            
            # Use context if provided and has plan_ahead data, otherwise use server state
            if context and context.get("type") == "plan_ahead" and isinstance(context.get("data"), dict):
                meal_plan = context["data"].get("meal_plan") or server_state.get("meal_plan", {})
                shopping_list = context["data"].get("shopping_list") or server_state.get("shopping_list", [])
            else:
                meal_plan = plan_data.get("meal_plan") or server_state.get("meal_plan", {})
                shopping_list = plan_data.get("shopping_list") or server_state.get("shopping_list", [])
            
            # 1. Extract modification intent from user input (semantic understanding)
            mod_intent = await self._extract_plan_modification_intent(
                user_input, history, meal_plan, user_timezone
            )
            
            # 2. Apply modification programmatically to plan JSON
            if mod_intent:
                meal_plan = self._apply_plan_modification(meal_plan, shopping_list, mod_intent)
                intent_action["data"]["_applied_modification"] = True
            
            # Update intent_action data with current (possibly modified) state
            intent_action["data"]["meal_plan"] = meal_plan
            intent_action["data"]["shopping_list"] = shopping_list
            
            # Add current date and next week info so AI knows the actual dates
            now = self._now_in_timezone(user_timezone)
            today = now.date()
            days_ahead = (7 - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            next_monday = today + timedelta(days=days_ahead)
            next_sunday = next_monday + timedelta(days=6)
            
            context_msg += f"\n\nTODAY'S DATE: {today.strftime('%Y-%m-%d (%A)')}"
            context_msg += f"\nNEXT WEEK: Monday {next_monday.strftime('%Y-%m-%d')} to Sunday {next_sunday.strftime('%Y-%m-%d')}"
            context_msg += "\n\n=== PLAN_AHEAD MODE ==="
            context_msg += "\nYour task: Help the user plan meals for the coming week."
            context_msg += "\n\nCRITICAL REQUIREMENT - PLAN_JSON OUTPUT:"
            context_msg += "\nYou MUST end EVERY response with a PLAN_JSON line showing the COMPLETE final plan."
            context_msg += "\nFormat: PLAN_JSON: {\"meal_plan\": {\"YYYY-MM-DD\": \"meal name\", ...}, \"shopping_list\": [\"item1\", ...]}"
            context_msg += "\nExample: PLAN_JSON: {\"meal_plan\": {\"2025-02-10\": \"Stir Fry\", \"2025-02-12\": \"Tacos\"}, \"shopping_list\": [\"beef\", \"rice\"]}"
            context_msg += "\n\nWhen to save: When user says 'save' or 'add to schedule', add a line: SAVE_TO_SCHEDULE"
            if meal_plan or shopping_list:
                context_msg += f"\n\nCURRENT PLAN STATE (THIS IS THE TRUTH):"
                context_msg += f"\nmeal_plan={json.dumps(meal_plan, ensure_ascii=False)}"
                context_msg += f"\nshopping_list={json.dumps(shopping_list, ensure_ascii=False)}"
                context_msg += "\n\nIMPORTANT RULES FOR PLAN_JSON OUTPUT:"
                context_msg += "\n1. COPY ALL dates and meals from CURRENT PLAN STATE above."
                context_msg += "\n2. ONLY modify what the user explicitly asked to change."
                context_msg += "\n3. If user says 'remove [day]', DELETE only that date from the plan."
                context_msg += "\n4. If user says 'change [day] to [meal]', UPDATE only that date."
                context_msg += "\n5. If user says 'add [meal] on [day]', ADD that date."
                context_msg += "\n6. DO NOT invent, modify, or hallucinate meals for dates the user didn't mention."
                context_msg += "\n7. PRESERVE the exact meal names from CURRENT PLAN STATE for all unchanged dates."
        elif intent_action.get('data', {}).get('suggestion'):
            context_msg += f"\nSuggestion: {intent_action['data']['suggestion']}"
        
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
                "maxOutputTokens": 2048  # Increased from 500 to handle longer responses with recipe suggestions
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

                # PLAN_AHEAD: parse PLAN_JSON from response and merge into action_data; strip from user-visible response
                if intent_action['action'] == "PLAN_AHEAD":
                    plan_json_marker = "PLAN_JSON:"
                    parsed_plan = None
                    
                    # Try to find PLAN_JSON marker
                    if plan_json_marker in response_text:
                        try:
                            idx = response_text.find(plan_json_marker)
                            json_str = response_text[idx + len(plan_json_marker):].strip()
                            # Trim to first complete JSON object (in case of trailing text)
                            brace = json_str.find("{")
                            if brace >= 0:
                                depth = 0
                                end = brace
                                for i, c in enumerate(json_str[brace:], start=brace):
                                    if c == "{":
                                        depth += 1
                                    elif c == "}":
                                        depth -= 1
                                        if depth == 0:
                                            end = i
                                            break
                                json_str = json_str[brace:end + 1]
                            parsed = json.loads(json_str)
                            if isinstance(parsed.get("meal_plan"), dict) or isinstance(parsed.get("shopping_list"), list):
                                parsed_plan = parsed
                                # Strip the PLAN_JSON line from what we show the user
                                response_text = response_text[:idx].rstrip()
                        except (json.JSONDecodeError, ValueError) as parse_err:
                            logger.warning(f"PLAN_AHEAD: could not parse PLAN_JSON from response: {parse_err}")
                    
                    # If no PLAN_JSON found, try programmatic removal detection first (user confirmed "Yes" -> remove)
                    if parsed_plan is None:
                        current_meal_plan = intent_action["data"].get("meal_plan", {})
                        current_shopping_list = intent_action["data"].get("shopping_list", [])
                        # Detect "Tuesday has been removed" / "plan for Tuesday... removed" in response
                        parsed_plan = self._try_detect_removal_from_response(
                            response_text, user_input, current_meal_plan, current_shopping_list, user_timezone
                        )
                    # Then try LLM extraction
                    if parsed_plan is None:
                        current_meal_plan = intent_action["data"].get("meal_plan", {})
                        current_shopping_list = intent_action["data"].get("shopping_list", [])
                        parsed_plan = await self._extract_plan_from_text(
                            response_text, 
                            user_input, 
                            history, 
                            user_timezone=user_timezone,
                            current_meal_plan=current_meal_plan,
                            current_shopping_list=current_shopping_list,
                        )
                    
                    # Update action_data and server state if we got a plan (from PLAN_JSON/extraction) OR we applied modification
                    if parsed_plan or intent_action["data"].get("_applied_modification"):
                        # If we got parsed_plan from PLAN_JSON/extraction, use it; else keep programmatic result
                        if parsed_plan:
                            if isinstance(parsed_plan.get("meal_plan"), dict):
                                intent_action["data"]["meal_plan"] = parsed_plan["meal_plan"]
                            if isinstance(parsed_plan.get("shopping_list"), list):
                                intent_action["data"]["shopping_list"] = parsed_plan["shopping_list"]
                        
                        # Update server state (replace completely, not merge)
                        meal_plan_final = intent_action["data"].get("meal_plan", {})
                        shopping_list_final = intent_action["data"].get("shopping_list", [])
                        update_plan_state(
                            owner_id=owner_id,
                            meal_plan=meal_plan_final,
                            shopping_list=shopping_list_final,
                            merge=False,  # Replace completely to allow removal
                        )

                        # Real-time write to schedule during conversation (always persist, even if empty - to clear schedule)
                        from app.storage.pipeline_storage import _default_storage
                        existing_id = plan_state.get("schedule_id")
                        
                        if meal_plan_final or shopping_list_final or existing_id:
                            sid = await _default_storage.create_or_update_meal_plan_schedule(
                                owner_id=owner_id,
                                meal_plan=meal_plan_final,
                                shopping_list=shopping_list_final,
                                existing_schedule_id=existing_id,
                                event_type="meal_plan_draft",
                                user_timezone=user_timezone,
                            )
                            if sid:
                                intent_action["data"]["schedule_id"] = sid
                                update_plan_state(owner_id=owner_id, schedule_id=sid, merge=True)
                                logger.info(f"Meal plan persisted to schedule id={sid}")
                            else:
                                # Schedule write failed completely, clear stale schedule_id from memory
                                if "schedule_id" in intent_action.get("data", {}):
                                    del intent_action["data"]["schedule_id"]
                                update_plan_state(owner_id=owner_id, schedule_id=None, merge=True)

                # PLAN_AHEAD: if LLM output SAVE_TO_SCHEDULE, finalize schedule (update draft to shopping_list)
                if intent_action['action'] == "PLAN_AHEAD" and "SAVE_TO_SCHEDULE" in response_text:
                    plan_data = intent_action.get("data") or {}
                    shopping_list = plan_data.get("shopping_list") or []
                    meal_plan = plan_data.get("meal_plan") or {}
                    if shopping_list or meal_plan:
                        from app.storage.pipeline_storage import _default_storage
                        existing_id = plan_data.get("schedule_id") or plan_state.get("schedule_id")
                        schedule_id = await _default_storage.create_or_update_meal_plan_schedule(
                            owner_id=owner_id,
                            meal_plan=meal_plan,
                            shopping_list=shopping_list,
                            existing_schedule_id=existing_id,
                            event_type="shopping_list",
                            user_timezone=user_timezone,
                        )
                        if schedule_id is not None:
                            intent_action["data"]["schedule_id"] = schedule_id
                            intent_action["data"]["saved_to_schedule"] = True
                            logger.info(f"PLAN_AHEAD: saved shopping list to schedule id={schedule_id}")
                    response_text = response_text.replace("SAVE_TO_SCHEDULE", "").strip()

                # Clean internal flags before returning
                if intent_action.get("data"):
                    intent_action["data"].pop("_applied_modification", None)

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

