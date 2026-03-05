from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import httpx
import json
import logging
import asyncio
from app.core.config import settings

logger = logging.getLogger(__name__)

class Intent(str, Enum):
    SEARCH = "SEARCH"
    UPDATE = "UPDATE"
    PLAN_EAT_OUT = "PLAN_EAT_OUT"
    PLAN_AHEAD = "PLAN_AHEAD"
    COOKING_STEPS = "COOKING_STEPS"
    GENERAL = "GENERAL"


class IntentClassificationResult(BaseModel):
    intent: Intent = Field(..., description="The detected intent of the user. MUST be one of: SEARCH, UPDATE, PLAN_EAT_OUT, PLAN_AHEAD, COOKING_STEPS, GENERAL.")
    confidence: float = Field(..., description="Confidence score from 0.0 to 1.0.")
    reasoning: str = Field(..., description="Brief explanation of why this intent was chosen.")

class IntentClassifier:
    """
    Classifier that uses Gemini API to detect user intent from natural language queries.
    """

    SYSTEM_PROMPT = """
You are an expert Intent Classifier for a Home AI Agent. Your task is to analyze user input and classify it into one of the following intents. CRITICAL: Use the conversation history to resolve ambiguity.

1. **SEARCH**: The user wants to find something in their STORED inventory, documents, or receipts. This is for looking up items that already exist in the system.
   - Example: "Find my Costco receipt from last week", "Do I have any eggs in my fridge?", "Show me the tax documents from 2024".
   - NOT for: "What plan do I have?" or "What do I have planned?" — those mean meal plan, use PLAN_AHEAD.

2. **UPDATE**: The user wants to update or modify an item that ALREADY EXISTS in the system (documents, receipts, inventory items). This is for editing PERSISTED data, NOT for editing a meal plan during a planning conversation.
   - Example: "Update the quantity of eggs in my inventory", "Change the expiration date of the milk I uploaded", "Modify the category of apples in my receipt".
   - NOT for: "Change Monday's meal to pork" or "Swap Wednesday's dinner" — when in a meal planning conversation, those are PLAN_AHEAD.

3. **PLAN_EAT_OUT**: The user wants to plan a meal outside the home. This includes restaurant reservations, looking for places to eat, or checking restaurant information.
   - Example: "Book a table for two at a sushi place", "Where should we go for dinner tonight?", "Check the menu for the Italian restaurant nearby".

4. **PLAN_AHEAD**: The user wants to PLAN AHEAD for a period or a specific date: decide what to cook (possibly via dialogue), then get a meal plan and shopping list of ingredients to buy, or save to schedule. Plan Cook Home is a sub-flow under PLAN_AHEAD (cooking at home, using inventory or deciding what to cook). This includes:
   - Cooking at home using existing inventory: "What can I cook with tomatoes and eggs?", "Generate a recipe with what I have", "What to make from my fridge".
   - Planning to cook at home on a date but not yet decided what to cook: "I'm planning to cook at home next Monday but I don't know what to cook", "下周一想在家做饭但不知道做什么".
   - Starting or continuing a meal plan: "What should I eat next week?", "Help me plan next week's meals", "Yes", "What's your recommendation?"
   - Viewing the current meal plan: "What plan do I have?", "What do I have now?", "Show me my plan", "What's my current plan?"
   - Editing the meal plan: "Change Monday's meal to pork", "Swap Sunday's dinner", "Wednesday change to something spicy"
   - CRITICAL: If recent conversation history shows the user is in the middle of planning meals, phrases like "what I have", "what do I have now", "what plan" mean "show my meal plan" — use PLAN_AHEAD, NOT SEARCH.
   - When in meal planning context, "change X's meal" = PLAN_AHEAD, NOT UPDATE.

5. **COOKING_STEPS**: The user wants step-by-step instructions on how to cook a specific dish. Typically asked after discussing a meal plan or a specific dish.
   - Example: "怎么做呢", "怎么做", "怎么做这道菜", "how to cook this", "how do I make this", "做法是什么", "教我怎么做", "步骤是什么", "cooking steps", "recipe", "how to prepare it".
   - CRITICAL: When the conversation context involves meal planning and the user asks how to cook something, ALWAYS classify as COOKING_STEPS, NOT GENERAL.

6. **GENERAL**: Basic greetings, general conversation, or queries that don't fit the above tasks.
   - Example: "Hello", "How are you?", "What can you do?".

PRIORITY RULE: If the recent conversation (last few turns) is about planning meals for next week, and the user says something ambiguous like "what I have" or "change Monday's meal", prefer PLAN_AHEAD over SEARCH or UPDATE.
COOKING_STEPS PRIORITY: If the user asks how to cook something (怎么做, how to make, steps, recipe instructions) — especially when recent context involves meal planning — ALWAYS classify as COOKING_STEPS, not GENERAL.

Respond ONLY with a JSON object that strictly adheres to the following schema:
{
  "intent": "SEARCH" | "UPDATE" | "PLAN_EAT_OUT" | "PLAN_AHEAD" | "COOKING_STEPS" | "GENERAL",
  "confidence": number (0.0 to 1.0),
  "reasoning": "string"
}
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    async def classify(
        self,
        user_input: str,
        history: List[Dict[str, str]] = None,
        session_mode: Optional[str] = None,
    ) -> "IntentClassificationResult":
        """
        Classifies the user input into an Intent, considering conversation history for context.

        Args:
            user_input:    The latest user message.
            history:       Recent conversation turns for context.
            session_mode:  Optional active session mode (e.g. "PLANNING").
                           When "PLANNING", injects a high-priority context block so the LLM
                           knows the user is mid-session and can weigh PLAN_AHEAD accordingly —
                           without keyword-based overrides in the caller.
        """
        # Build session-mode context block (injected BEFORE history)
        session_block = ""
        if session_mode == "PLANNING":
            session_block = (
                "=== ACTIVE SESSION: MEAL PLANNING ===\n"
                "The user is currently in the middle of a meal-planning session. "
                "They may have an in-progress draft plan.\n"
                "- Prefer PLAN_AHEAD for any message that COULD relate to the planning conversation "
                "(e.g. preferences, changes, approvals, vague follow-ups).\n"
                "- Only switch away from PLAN_AHEAD if the user CLEARLY signals a topic change "
                "(e.g. 'forget the plan, search for X', 'let's stop planning', 'check my fridge').\n"
                "- Ambiguous phrases like '随便', '你来', '按之前的来', '你看着办', '这个', '就这样', "
                "'听起来不错', '可以', '行', '好的', '没问题', 'sure', 'ok', 'sounds good', 'that works' "
                "should be interpreted as PLAN_AHEAD (confirmation or preference in planning context).\n"
                "=== END SESSION CONTEXT ===\n\n"
            )

        # Construct context from history
        context_text = ""
        if history:
            # Take last 5 messages for context to keep it relevant but concise
            recent_history = history[-5:]
            context_text = "Recent conversation history for context:\n"
            for msg in recent_history:
                role = "User" if msg["role"] == "user" else "Assistant"
                context_text += f"{role}: {msg['content']}\n"
            context_text += "\n"

        full_input = f"{session_block}{context_text}Current User Input: {user_input}"

        payload = {
            "contents": [{"parts": [{"text": full_input}]}],
            "systemInstruction": {"parts": [{"text": self.SYSTEM_PROMPT}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.0
            }
        }

        headers = {'Content-Type': 'application/json'}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()

                json_string = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')
                if not json_string:
                    raise ValueError("Gemini API response missing text content.")

                parsed_json = json.loads(json_string)
                return IntentClassificationResult(**parsed_json)

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            # Fallback to GENERAL if classification fails
            return IntentClassificationResult(
                intent=Intent.GENERAL,
                confidence=0.0,
                reasoning=f"Error during classification: {str(e)}"
            )

# Singleton instance
intent_classifier = IntentClassifier()

