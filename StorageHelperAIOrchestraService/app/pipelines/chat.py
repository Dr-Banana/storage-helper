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
If the intent is GENERAL: Be friendly and helpful.

Respond naturally in the same language as the user.
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"


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
        # Initialise cooking-steps data containers (populated later if intent == COOKING_STEPS)
        _cs_injected_context: str = ""
        _cs_action_data: Dict[str, Any] = {}
        active_cooking_ctx: Optional[Dict[str, Any]] = None

        # 0. Check for context-based intent (e.g. Correction)
        # If we have explicit correction context, we bypass standard intent classification
        is_correction = False
        if context and context.get("type") == "correction" and context.get("data"):
            intent_result = type('obj', (object,), {'intent': Intent.UPDATE, 'reasoning': 'User is correcting items based on provided context', 'confidence': 1.0})
            intent_action = {'action': 'CORRECTION_MODE', 'data': {}, 'message': 'Processing correction...'}
            is_correction = True
        else:
            # Detect whether user is currently in a meal-planning session
            plan_state = get_plan_state(owner_id)
            in_plan_ahead_flow = (
                (context and context.get("type") == "plan_ahead")
                or bool(plan_state.get("meal_plan") or plan_state.get("shopping_list"))
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

            # 1. Classify intent — inject session context so the LLM can decide
            #    stickiness intelligently instead of relying on a keyword list.
            session_mode = "PLANNING" if in_plan_ahead_flow else None
            active_cooking_ctx = plan_state.get("cooking_context")  # for DISCUSSING_RECIPE block
            intent_result = await intent_classifier.classify(
                user_input,
                history=history,
                session_mode=session_mode,
                cooking_context=active_cooking_ctx,
            )

            # 1b. Lightweight safety net: if we're in a plan-ahead flow and the LLM returns
            #     COOKING_STEPS but downgrades to GENERAL (rare edge case), correct it.
            #     NOTE: broad "stickiness" is now handled by the LLM via session_mode above.
            cooking_step_phrases = (
                "怎么做", "怎么烹饪", "如何做", "做法", "步骤", "教我做",
                "how to cook", "how to make", "how do i cook", "how do i make",
                "cooking steps", "recipe steps", "how to prepare",
            )
            user_lower = user_input.strip().lower()
            is_cooking_query = any(phrase in user_lower for phrase in cooking_step_phrases)
            if in_plan_ahead_flow and is_cooking_query and intent_result.intent == Intent.GENERAL:
                old_intent = intent_result.intent
                intent_result = type(
                    "IntentResult",
                    (),
                    {
                        "intent": Intent.COOKING_STEPS,
                        "reasoning": "User asked how to cook while in meal planning context",
                        "confidence": 0.9,
                    },
                )()
                logger.info(f"Cooking-steps override: {old_intent} -> COOKING_STEPS for: {user_input[:50]}")

            # 2a. COOKING_STEPS: short-circuit before route_by_intent to pass history.
            #
            # Special case — "confirm + cooking" compound instruction:
            # If the user is in an active draft plan AND their message contains both
            # an implicit/explicit confirmation AND a cooking query (e.g. "可以，周三牛排怎么做"),
            # we first run PlanAheadPipeline to confirm/save the draft, then generate cooking steps.
            _confirm_phrases = (
                "可以", "行", "好的", "没问题", "确认", "保存", "好", "就这样", "就这个",
                "听起来不错", "挺好", "就按这个", "就这么定", "ok", "sure", "yes", "confirm",
                "looks good", "sounds good", "that works", "go ahead",
            )
            _is_draft_context = (
                in_plan_ahead_flow
                and (context and context.get("type") == "plan_ahead")
                and (context.get("data") or {}).get("is_draft", False)
            )
            _has_confirm_signal = any(p in user_lower for p in _confirm_phrases)

            if (
                intent_result.intent == Intent.COOKING_STEPS
                and _is_draft_context
                and _has_confirm_signal
            ):
                # Step 1: confirm the draft via PlanAheadPipeline
                logger.info(
                    f"[COMPOUND] Confirm+CookingSteps detected for: {user_input[:60]}"
                )
                try:
                    from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
                    from app.storage.pipeline_storage import _default_storage
                    _confirm_pipeline = PlanAheadPipeline(gemini_api_url=self.api_url)
                    _confirm_result = await _confirm_pipeline.execute(
                        owner_id=owner_id,
                        user_input="确认保存计划",  # explicit confirm signal so LLM picks action=confirm
                        history=history,
                        user_timezone=user_timezone,
                        storage_client=_default_storage,
                        context=context,
                        intent_result=intent_result,
                    )
                    # Update context with freshly confirmed plan data
                    context = {
                        "type": "plan_ahead",
                        "data": _confirm_result.get("action_data") or (context.get("data") or {}),
                    }
                    logger.info("[COMPOUND] Draft confirmed; proceeding to cooking steps.")
                except Exception as _conf_err:
                    logger.warning(f"[COMPOUND] Draft confirm step failed: {_conf_err}", exc_info=True)

            # RECIPE_QA: targeted parameter query about an already-discussed dish.
            # Skip CookingStepsAgent entirely — inject cooking_context and let LLM answer directly.
            if intent_result.intent == Intent.RECIPE_QA:
                if active_cooking_ctx and active_cooking_ctx.get("dish_name"):
                    _qa_dish = active_cooking_ctx["dish_name"]
                    _qa_steps = active_cooking_ctx.get("steps") or []
                    if _qa_steps:
                        _qa_steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_qa_steps))
                        _cs_injected_context = (
                            f"\n\n=== RECIPE CONTEXT FOR 「{_qa_dish}」 ===\n"
                            f"{_qa_steps_text}\n"
                            "=== END RECIPE CONTEXT ===\n\n"
                            "The user is asking a specific follow-up question about this recipe. "
                            "Answer the specific question ONLY (e.g. give the exact ratio, temperature, "
                            "timing, or substitution they asked for). "
                            "Use the recipe as the primary source, then SUPPLEMENT with your own culinary "
                            "expertise for any missing specifics — never say 'the recipe doesn't include that'. "
                            "Be concise and direct. Respond in the user's language."
                        )
                    else:
                        # No stored steps — answer from general knowledge
                        _cs_injected_context = (
                            f"\n\n[The user is asking a follow-up about 「{_qa_dish}」. "
                            "Answer from your general culinary knowledge.]\n"
                        )
                else:
                    # No cooking context — treat as general
                    intent_result = type("IR", (), {
                        "intent": Intent.GENERAL,
                        "confidence": 0.7,
                        "reasoning": "RECIPE_QA with no active cooking context; falling back to GENERAL",
                    })()

            # COOKING_STEPS: agent acts as a "data fetcher" — structured steps are injected
            # into the LLM system prompt so Gemini can answer naturally and handle follow-ups.
            # This replaces the old hard short-circuit that returned a fixed template string.
            if intent_result.intent == Intent.COOKING_STEPS:
                try:
                    from app.agents.cooking_steps_agent import CookingStepsAgent as _CSAgent
                    _cs_result = await _CSAgent().execute(
                        user_input=user_input,
                        owner_id=owner_id,
                        context=context,
                        history=history,
                    )
                    _cs_data = _cs_result.get("data") or {}
                    _cs_action_data = _cs_data
                    _steps = _cs_data.get("cooking_steps", [])
                    _dish = _cs_data.get("dish_name", "")

                    if _steps:
                        _steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_steps))
                        _saved_note = (
                            "\n[Steps saved to meal plan — user can view them in the dish detail card]"
                            if _cs_data.get("saved") else ""
                        )
                        _cs_injected_context = (
                            f"\n\n=== RECIPE DATA FOR 「{_dish}」 ===\n"
                            f"{_steps_text}"
                            f"{_saved_note}\n"
                            "=== END RECIPE DATA ===\n\n"
                            "Use the recipe data above as the PRIMARY source to answer the user's question. "
                            "If the recipe data is missing specific details the user is asking about "
                            "(e.g. exact sauce ratios, temperatures, cooking times, ingredient substitutions), "
                            "SUPPLEMENT with your own culinary expertise to give a complete, helpful answer — "
                            "do NOT say 'the recipe doesn't include that information'. "
                            "Present steps clearly if asked for the full recipe, or answer only the specific "
                            "part they asked about. Respond conversationally in the user's language."
                        )
                        # Persist cooking context so follow-ups skip re-generation
                        from app.modules.plan_ahead_state import update_plan_state
                        update_plan_state(
                            owner_id=owner_id,
                            cooking_context={"dish_name": _dish, "steps": _steps},
                        )
                    else:
                        # Agent couldn't find/generate steps — fall through to LLM with a nudge
                        _cs_injected_context = (
                            f"\n\n[CookingStepsAgent could not find steps: "
                            f"{_cs_result.get('message', '')}]\n"
                            "Tell the user you couldn't identify the dish or find cooking steps, "
                            "and ask them to clarify which dish they mean."
                        )
                except Exception as _cs_err:
                    logger.error(f"[COOKING_STEPS] Agent failed: {_cs_err}", exc_info=True)
                    _cs_injected_context = (
                        "\n\n[CookingStepsAgent encountered an error. "
                        "Apologise briefly and ask the user to try again.]\n"
                    )

            # 2b. Get intent-specific mock action/data (pass context for e.g. plan_ahead state)
            intent_action = await route_by_intent(
                intent_result.intent, user_input, owner_id, context=context
            )

        # 3. Generate response using Gemini
        # We include the detected intent and mock action in the prompt to guide the AI's response
        system_instruction = self.SYSTEM_PROMPT.format(
            intent=intent_result.intent.value,
            reasoning=intent_result.reasoning
        )

        # Inject cooking steps data (if agent ran) into system prompt so LLM can answer naturally
        if _cs_injected_context:
            system_instruction += _cs_injected_context

        # Add context about the specific action we're taking
        context_msg = f"\nSystem Action: {intent_action['message']}"

        # Inject active cooking context for GENERAL follow-up questions about a recipe
        # (e.g. "酱料比例是多少?" after the AI showed a recipe).
        # Skip if COOKING_STEPS or RECIPE_QA already injected the context above.
        if (
            not _cs_injected_context  # skip if already injected
            and intent_result.intent == Intent.GENERAL
            and active_cooking_ctx
            and active_cooking_ctx.get("dish_name")
        ):
            _ctx_dish = active_cooking_ctx["dish_name"]
            _ctx_steps = active_cooking_ctx.get("steps") or []
            if _ctx_steps:
                _ctx_steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_ctx_steps))
                context_msg += (
                    f"\n\n=== RECIPE CONTEXT (already shared with user) ===\n"
                    f"Dish: 「{_ctx_dish}」\n"
                    f"{_ctx_steps_text}\n"
                    "=== END RECIPE CONTEXT ===\n"
                    "Use the recipe above as the PRIMARY source to answer the follow-up question. "
                    "If the user is asking for a specific detail not in the recipe "
                    "(e.g. exact ratios, temperatures, substitutions, variations), "
                    "SUPPLEMENT with your own culinary expertise — do NOT say the recipe lacks that info. "
                    "Be concise and targeted; don't re-list all steps unless explicitly asked."
                )

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
            # Phase 3: delegate to PlanAheadPipeline (single structured LLM call).
            if SCHEDULING_AGENTS_AVAILABLE:
                try:
                    from app.pipelines.plan_ahead_pipeline import PlanAheadPipeline
                    from app.storage.pipeline_storage import _default_storage
                    _pipeline = PlanAheadPipeline(gemini_api_url=self.api_url)
                    return await _pipeline.execute(
                        owner_id=owner_id,
                        user_input=user_input,
                        history=history,
                        user_timezone=user_timezone,
                        storage_client=_default_storage,
                        context=context,
                        intent_result=intent_result,
                    )
                except Exception as _pipe_err:
                    logger.error(f"[PLAN_AHEAD_PIPELINE] Pipeline failed: {_pipe_err}", exc_info=True)
                    return {
                        "response": "抱歉，饮食计划功能暂时遇到问题，请稍后重试。",
                        "intent": intent_result.intent,
                        "confidence": intent_result.confidence,
                        "reasoning": intent_result.reasoning,
                        "action": "PLAN_AHEAD",
                        "action_data": intent_action.get("data") or {},
                    }

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
                "maxOutputTokens": 4096
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

                # For COOKING_STEPS turns, surface the structured agent data so the
                # frontend can still update the dish detail card (schedule_id, saved, etc.)
                # RECIPE_QA goes straight to LLM; action stays GENERAL (no agent data to surface).
                final_action = intent_action['action']
                final_action_data = intent_action['data']
                if intent_result.intent == Intent.COOKING_STEPS and _cs_action_data:
                    final_action = "COOKING_STEPS"
                    final_action_data = _cs_action_data
                elif intent_result.intent == Intent.RECIPE_QA:
                    final_action = "RECIPE_QA"

                return {
                    "response": response_text,
                    "intent": intent_result.intent,
                    "confidence": intent_result.confidence,
                    "reasoning": intent_result.reasoning,
                    "action": final_action,
                    "action_data": final_action_data,
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

